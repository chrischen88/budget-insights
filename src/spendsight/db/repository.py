"""Repository functions: the only place that reads/writes tables directly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import duckdb
import pandas as pd

AccountKind = Literal["credit_card", "checking"]

# Columns `insert_transactions` expects in its staged DataFrame.
TRANSACTION_COLUMNS = (
    "id",
    "txn_date",
    "post_date",
    "amount_cents",
    "raw_description",
    "chase_category",
    "chase_type",
    "memo",
)


@dataclass(frozen=True)
class Account:
    id: str
    kind: AccountKind
    display_name: str
    last4: str | None


def get_account(conn: duckdb.DuckDBPyConnection, account_id: str) -> Account | None:
    row = conn.execute(
        "SELECT id, kind, display_name, last4 FROM accounts WHERE id = ?", [account_id]
    ).fetchone()
    if row is None:
        return None
    return Account(id=row[0], kind=row[1], display_name=row[2], last4=row[3])


def list_accounts(conn: duckdb.DuckDBPyConnection) -> list[Account]:
    rows = conn.execute(
        "SELECT id, kind, display_name, last4 FROM accounts ORDER BY display_name"
    ).fetchall()
    return [Account(id=r[0], kind=r[1], display_name=r[2], last4=r[3]) for r in rows]


def ensure_account(conn: duckdb.DuckDBPyConnection, account: Account) -> None:
    """Create the account if missing. An existing account keeps its display name."""
    conn.execute(
        "INSERT INTO accounts (id, kind, display_name, last4) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (id) DO NOTHING",
        [account.id, account.kind, account.display_name, account.last4],
    )


def find_import_by_sha(conn: duckdb.DuckDBPyConnection, file_sha256: str) -> str | None:
    row = conn.execute("SELECT id FROM imports WHERE file_sha256 = ?", [file_sha256]).fetchone()
    return None if row is None else str(row[0])


def insert_import(
    conn: duckdb.DuckDBPyConnection,
    *,
    import_id: str,
    account_id: str,
    filename: str,
    file_sha256: str,
    row_count: int,
) -> None:
    conn.execute(
        "INSERT INTO imports (id, account_id, filename, file_sha256, imported_at, row_count) "
        "VALUES (?, ?, ?, ?, current_timestamp, ?)",
        [import_id, account_id, filename, file_sha256, row_count],
    )


def insert_transactions(
    conn: duckdb.DuckDBPyConnection,
    *,
    import_id: str,
    account_id: str,
    staged: pd.DataFrame,
) -> int:
    """Insert staged rows (columns: TRANSACTION_COLUMNS) whose ID is not already stored.

    Returns how many rows were new.
    """
    missing = set(TRANSACTION_COLUMNS) - set(staged.columns)
    if missing:
        raise ValueError(f"staged transactions missing columns: {sorted(missing)}")
    if staged.empty:
        return 0
    conn.register("staged_transactions", staged)
    try:
        conn.execute(
            """
            INSERT INTO transactions (
                id, account_id, import_id, txn_date, post_date, amount_cents,
                raw_description, chase_category, chase_type, memo
            )
            SELECT
                CAST(id AS TEXT), ?, ?, CAST(txn_date AS DATE), CAST(post_date AS DATE),
                CAST(amount_cents AS BIGINT), CAST(raw_description AS TEXT),
                CAST(chase_category AS TEXT), CAST(chase_type AS TEXT), CAST(memo AS TEXT)
            FROM staged_transactions
            ON CONFLICT (id) DO NOTHING
            """,
            [account_id, import_id],
        )
    finally:
        conn.unregister("staged_transactions")
    row = conn.execute(
        "SELECT count(*) FROM transactions WHERE import_id = ?", [import_id]
    ).fetchone()
    return int(row[0]) if row else 0


def unpaired_transactions(conn: duckdb.DuckDBPyConnection) -> list[dict[str, object]]:
    """Rows eligible for transfer pairing: not yet paired and not already marked a transfer."""
    cursor = conn.execute(
        """
        SELECT t.id, t.account_id, a.kind AS account_kind, a.last4 AS account_last4,
               t.txn_date, t.amount_cents, t.raw_description, t.chase_type
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.transfer_pair_id IS NULL AND NOT t.is_transfer
        ORDER BY t.txn_date, t.id
        """
    )
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def mark_transfer_candidates(conn: duckdb.DuckDBPyConnection, ids: list[str]) -> None:
    if not ids:
        return
    conn.execute(
        "UPDATE transactions SET transfer_candidate = TRUE WHERE id IN (SELECT unnest(?))",
        [ids],
    )


def save_transfer_pairs(conn: duckdb.DuckDBPyConnection, pairs: list[tuple[str, str]]) -> None:
    """Mark both sides of each (outflow_id, inflow_id) pair as a transfer, linked to each other."""
    if not pairs:
        return
    links = pd.DataFrame(
        {
            "id": [a for a, _ in pairs] + [b for _, b in pairs],
            "pair_id": [b for _, b in pairs] + [a for a, _ in pairs],
        }
    )
    conn.register("staged_transfer_links", links)
    try:
        conn.execute(
            """
            UPDATE transactions
            SET is_transfer = TRUE, transfer_candidate = TRUE, transfer_pair_id = s.pair_id
            FROM staged_transfer_links s
            WHERE transactions.id = s.id
            """
        )
    finally:
        conn.unregister("staged_transfer_links")


def count_unpaired_transfer_candidates(conn: duckdb.DuckDBPyConnection) -> int:
    row = conn.execute(
        "SELECT count(*) FROM transactions WHERE transfer_candidate AND NOT is_transfer"
    ).fetchone()
    return int(row[0]) if row else 0


def chase_category_mapping(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT chase_category, category_id FROM chase_category_mappings"
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def rows_for_categorization(
    conn: duckdb.DuckDBPyConnection,
) -> list[tuple[str, str, int | None, str | None]]:
    """(id, raw_description, merchant_id, chase_category) for every row the cascade may set.

    Locked rows (per-transaction user overrides) are never returned.
    """
    rows = conn.execute(
        """
        SELECT id, raw_description, merchant_id, chase_category FROM transactions
        WHERE NOT category_locked
        ORDER BY id
        """
    ).fetchall()
    return [
        (
            str(r[0]),
            str(r[1]),
            None if r[2] is None else int(r[2]),
            None if r[3] is None else str(r[3]),
        )
        for r in rows
    ]


@dataclass(frozen=True)
class ClassifierRow:
    id: str
    key: str  # merchant normalized_key, or the raw description without a merchant
    amount_cents: int
    chase_category: str | None
    category_id: int | None
    locked: bool  # a per-transaction user override


def classifier_rows(conn: duckdb.DuckDBPyConnection) -> list[ClassifierRow]:
    """Every transaction with the classifier's features and its current category."""
    rows = conn.execute(
        """
        SELECT t.id, COALESCE(m.normalized_key, t.raw_description), t.amount_cents,
               t.chase_category, t.category_id, t.category_locked
        FROM transactions t
        LEFT JOIN merchants m ON m.id = t.merchant_id
        ORDER BY t.id
        """
    ).fetchall()
    return [
        ClassifierRow(
            str(r[0]),
            str(r[1]),
            int(r[2]),
            None if r[3] is None else str(r[3]),
            None if r[4] is None else int(r[4]),
            bool(r[5]),
        )
        for r in rows
    ]


def category_rules(conn: duckdb.DuckDBPyConnection) -> list[tuple[int, str, int, int]]:
    """(id, pattern, category_id, priority) for every rule."""
    rows = conn.execute(
        "SELECT id, pattern, category_id, COALESCE(priority, 100) FROM category_rules"
    ).fetchall()
    return [(int(r[0]), str(r[1]), int(r[2]), int(r[3])) for r in rows]


def merchant_defaults(
    conn: duckdb.DuckDBPyConnection,
) -> list[tuple[int, int, str, float | None]]:
    """(merchant_id, default_category_id, source, confidence) where a default is set."""
    rows = conn.execute(
        """
        SELECT id, default_category_id, source, confidence FROM merchants
        WHERE default_category_id IS NOT NULL AND source IS NOT NULL
        """
    ).fetchall()
    return [(int(r[0]), int(r[1]), str(r[2]), None if r[3] is None else float(r[3])) for r in rows]


def save_category_assignments(conn: duckdb.DuckDBPyConnection, assignments: pd.DataFrame) -> int:
    """Write (transaction_id, category_id, source, conf) rows. Returns rows changed.

    Locked rows (user overrides) are never touched, whatever the input says.
    """
    if assignments.empty:
        return 0
    conn.register("staged_categories", assignments)
    try:
        changed = conn.execute(
            """
            UPDATE transactions
            SET category_id     = CAST(s.category_id AS INTEGER),
                category_source = CAST(s.source AS TEXT),
                category_conf   = CAST(s.conf AS DOUBLE)
            FROM staged_categories s
            WHERE transactions.id = s.transaction_id
              AND NOT transactions.category_locked
              AND (transactions.category_id IS DISTINCT FROM CAST(s.category_id AS INTEGER)
                   OR transactions.category_source IS DISTINCT FROM CAST(s.source AS TEXT)
                   OR transactions.category_conf IS DISTINCT FROM CAST(s.conf AS DOUBLE))
            RETURNING transactions.id
            """
        ).fetchall()
    finally:
        conn.unregister("staged_categories")
    return len(changed)


def category_exists(conn: duckdb.DuckDBPyConnection, category_id: int) -> bool:
    row = conn.execute("SELECT 1 FROM categories WHERE id = ?", [category_id]).fetchone()
    return row is not None


def set_transaction_category(
    conn: duckdb.DuckDBPyConnection, transaction_id: str, category_id: int
) -> bool:
    """User override for one transaction: locks it so the cascade never changes it."""
    if not category_exists(conn, category_id):
        raise ValueError(f"unknown category id {category_id}")
    changed = conn.execute(
        """
        UPDATE transactions
        SET category_id = ?, category_source = 'user', category_conf = 1.0,
            category_locked = TRUE
        WHERE id = ?
        RETURNING id
        """,
        [category_id, transaction_id],
    ).fetchall()
    return bool(changed)


def unpaired_transfer_candidates(conn: duckdb.DuckDBPyConnection) -> list[dict[str, object]]:
    """Rows that look like transfers but have no matching side yet (SPEC.md §5.3)."""
    cursor = conn.execute(
        """
        SELECT t.id, t.txn_date, a.display_name AS account, t.raw_description,
               t.amount_cents
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.transfer_candidate AND NOT t.is_transfer
        ORDER BY t.txn_date DESC, t.id
        """
    )
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def mark_as_transfer(conn: duckdb.DuckDBPyConnection, ids: list[str]) -> int:
    """User confirms unpaired candidates are transfers. Only candidates can be marked."""
    if not ids:
        return 0
    changed = conn.execute(
        """
        UPDATE transactions SET is_transfer = TRUE
        WHERE id IN (SELECT unnest(?)) AND transfer_candidate AND NOT is_transfer
        RETURNING id
        """,
        [ids],
    ).fetchall()
    return len(changed)


def transactions_without_merchant(conn: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    """(id, raw_description) for rows not yet linked to a merchant."""
    rows = conn.execute(
        "SELECT id, raw_description FROM transactions WHERE merchant_id IS NULL ORDER BY id"
    ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def link_merchants(conn: duckdb.DuckDBPyConnection, links: pd.DataFrame) -> int:
    """Create missing merchants by normalized_key and link transactions to them.

    `links` columns: transaction_id, normalized_key. New merchant rows carry only the key;
    clean_name/default category come later from the user or the LLM. Returns rows linked.
    """
    if links.empty:
        return 0
    conn.register("staged_merchant_links", links)
    try:
        conn.execute(
            """
            INSERT INTO merchants (normalized_key)
            SELECT DISTINCT CAST(normalized_key AS TEXT) FROM staged_merchant_links
            ON CONFLICT (normalized_key) DO NOTHING
            """
        )
        linked = conn.execute(
            """
            UPDATE transactions
            SET merchant_id = m.id
            FROM staged_merchant_links s
            JOIN merchants m ON m.normalized_key = s.normalized_key
            WHERE transactions.id = s.transaction_id AND transactions.merchant_id IS NULL
            RETURNING transactions.id
            """
        ).fetchall()
    finally:
        conn.unregister("staged_merchant_links")
    return len(linked)


# Reference columns without a foreign key (migration 008), checked here instead.
_UNENFORCED_REFERENCES = (
    ("categories.parent_id", "categories", "parent_id", "categories"),
    ("merchants.default_category_id", "merchants", "default_category_id", "categories"),
    ("transactions.merchant_id", "transactions", "merchant_id", "merchants"),
    ("transactions.category_id", "transactions", "category_id", "categories"),
    ("recurring_series.merchant_id", "recurring_series", "merchant_id", "merchants"),
    ("anomalies.transaction_id", "anomalies", "transaction_id", "transactions"),
)


def integrity_problems(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Describe any dangling references in columns the database doesn't enforce."""
    problems = []
    for label, table, column, target in _UNENFORCED_REFERENCES:
        row = conn.execute(
            f"""
            SELECT count(*) FROM {table} src
            WHERE src.{column} IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM {target} t WHERE t.id = src.{column})
            """
        ).fetchone()
        count = int(row[0]) if row else 0
        if count:
            problems.append(f"{label}: {count} row(s) point at a missing {target} row")
    return problems


@dataclass(frozen=True)
class CategoryOption:
    id: int
    name: str
    parent_name: str | None

    @property
    def label(self) -> str:
        return f"{self.parent_name} / {self.name}" if self.parent_name else self.name


def list_categories(conn: duckdb.DuckDBPyConnection) -> list[CategoryOption]:
    """Every category, grouped under its parent (parents first within each group)."""
    rows = conn.execute(
        """
        SELECT c.id, c.name, p.name AS parent_name
        FROM categories c LEFT JOIN categories p ON p.id = c.parent_id
        ORDER BY COALESCE(p.name, c.name), p.name IS NOT NULL, c.name
        """
    ).fetchall()
    return [CategoryOption(int(r[0]), str(r[1]), None if r[2] is None else str(r[2])) for r in rows]


@dataclass(frozen=True)
class TransactionQuery:
    start: date | None = None
    end: date | None = None
    account_ids: tuple[str, ...] | None = None  # None = all
    text: str = ""  # matched case-insensitively in description, merchant, memo
    category_id: int | None = None  # matches the category or any of its children
    uncategorized_only: bool = False
    include_transfers: bool = False
    limit: int = 500


def search_transactions(
    conn: duckdb.DuckDBPyConnection, q: TransactionQuery
) -> tuple[pd.DataFrame, int]:
    """Matching transactions, newest first, up to `q.limit`; plus the total match count."""
    where = ["TRUE"]
    params: list[object] = []
    if q.start is not None:
        where.append("t.txn_date >= ?")
        params.append(q.start)
    if q.end is not None:
        where.append("t.txn_date <= ?")
        params.append(q.end)
    if q.account_ids is not None:
        where.append("t.account_id IN (SELECT unnest(?::TEXT[]))")
        params.append(list(q.account_ids))
    if q.text.strip():
        where.append(
            "(strpos(lower(t.raw_description), lower(?)) > 0"
            " OR strpos(lower(COALESCE(m.clean_name, m.normalized_key, '')), lower(?)) > 0"
            " OR strpos(lower(COALESCE(t.memo, '')), lower(?)) > 0)"
        )
        params.extend([q.text.strip()] * 3)
    if q.uncategorized_only:
        where.append("t.category_id IS NULL")
    elif q.category_id is not None:
        where.append("(t.category_id = ? OR c.parent_id = ?)")
        params.extend([q.category_id, q.category_id])
    if not q.include_transfers:
        where.append("NOT t.is_transfer")
    cursor = conn.execute(
        f"""
        SELECT
            t.id, t.txn_date AS date, a.display_name AS account,
            COALESCE(m.clean_name, m.normalized_key, t.raw_description) AS merchant,
            t.raw_description, t.amount_cents, t.merchant_id,
            t.category_id, c.name AS category, p.name AS parent_category,
            t.category_source, t.category_conf, t.category_locked, t.is_transfer,
            count(*) OVER () AS total
        FROM transactions t
        JOIN accounts a        ON a.id = t.account_id
        LEFT JOIN merchants m  ON m.id = t.merchant_id
        LEFT JOIN categories c ON c.id = t.category_id
        LEFT JOIN categories p ON p.id = c.parent_id
        WHERE {" AND ".join(where)}
        ORDER BY t.txn_date DESC, t.id
        LIMIT ?
        """,
        [*params, q.limit],
    )
    columns = [d[0] for d in cursor.description]
    frame = pd.DataFrame(cursor.fetchall(), columns=columns)
    total = int(frame["total"].iloc[0]) if not frame.empty else 0
    return frame.drop(columns="total"), total


def transaction_merchant(conn: duckdb.DuckDBPyConnection, transaction_id: str) -> int | None:
    row = conn.execute(
        "SELECT merchant_id FROM transactions WHERE id = ?", [transaction_id]
    ).fetchone()
    if row is None:
        raise ValueError("unknown transaction")
    return None if row[0] is None else int(row[0])


def set_merchant_default(
    conn: duckdb.DuckDBPyConnection, merchant_id: int, category_id: int
) -> None:
    """User sets a merchant's default category (cascade step 3)."""
    if not category_exists(conn, category_id):
        raise ValueError(f"unknown category id {category_id}")
    changed = conn.execute(
        """
        UPDATE merchants SET default_category_id = ?, source = 'user', confidence = 1.0
        WHERE id = ? RETURNING id
        """,
        [category_id, merchant_id],
    ).fetchall()
    if not changed:
        raise ValueError("unknown merchant")


def clear_transaction_override(conn: duckdb.DuckDBPyConnection, transaction_id: str) -> bool:
    """Unlock a transaction so the cascade categorizes it again on its next run."""
    changed = conn.execute(
        "UPDATE transactions SET category_locked = FALSE WHERE id = ? AND category_locked "
        "RETURNING id",
        [transaction_id],
    ).fetchall()
    return bool(changed)


def category_snapshot(conn: duckdb.DuckDBPyConnection) -> dict[str, int | None]:
    """Every transaction's current category id, to see what a cascade run changed."""
    rows = conn.execute("SELECT id, category_id FROM transactions").fetchall()
    return {str(r[0]): None if r[1] is None else int(r[1]) for r in rows}


def merchant_transaction_ids(conn: duckdb.DuckDBPyConnection, merchant_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM transactions WHERE merchant_id = ?", [merchant_id]
    ).fetchall()
    return [str(r[0]) for r in rows]


def merchant_category_breakdown(
    conn: duckdb.DuckDBPyConnection, merchant_id: int, category_id: int
) -> tuple[int, int, int, int]:
    """(total, in category, locked elsewhere, ruled elsewhere) for a merchant's rows."""
    row = conn.execute(
        """
        SELECT
            count(*),
            count(*) FILTER (category_id = ?),
            count(*) FILTER (category_id IS DISTINCT FROM ? AND category_locked),
            count(*) FILTER (category_id IS DISTINCT FROM ? AND NOT category_locked
                             AND category_source = 'rule')
        FROM transactions WHERE merchant_id = ?
        """,
        [category_id, category_id, category_id, merchant_id],
    ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1]), int(row[2]), int(row[3])


def llm_cache_get(conn: duckdb.DuckDBPyConnection, prompt_hash: str) -> str | None:
    row = conn.execute(
        "SELECT CAST(response AS TEXT) FROM llm_cache WHERE prompt_hash = ?", [prompt_hash]
    ).fetchone()
    return None if row is None else str(row[0])


def llm_cache_put(conn: duckdb.DuckDBPyConnection, prompt_hash: str, response_json: str) -> None:
    conn.execute(
        "INSERT INTO llm_cache (prompt_hash, response, created_at) "
        "VALUES (?, CAST(? AS JSON), current_timestamp) ON CONFLICT (prompt_hash) DO NOTHING",
        [prompt_hash, response_json],
    )


def log_llm_call(
    conn: duckdb.DuckDBPyConnection,
    *,
    provider: str,
    model: str,
    prompt: str,
    prompt_version: int,
    cache_hit: bool,
    ok: bool,
    error: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO llm_calls (provider, model, prompt, prompt_version, cache_hit, ok, error,
                               input_tokens, output_tokens)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            provider,
            model,
            prompt,
            prompt_version,
            cache_hit,
            ok,
            error,
            input_tokens,
            output_tokens,
        ],
    )


def merchants_needing_names(conn: duckdb.DuckDBPyConnection) -> list[tuple[int, str]]:
    """(id, normalized_key) for merchants nobody has named yet, skipping transfer-only ones."""
    rows = conn.execute(
        """
        SELECT m.id, m.normalized_key FROM merchants m
        WHERE m.source IS NULL AND m.normalized_key IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM transactions t
              WHERE t.merchant_id = m.id AND NOT t.transfer_candidate AND NOT t.is_transfer
          )
        ORDER BY m.id
        """
    ).fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]


def save_llm_merchant_names(
    conn: duckdb.DuckDBPyConnection,
    rows: list[tuple[int, str | None, int | None, float | None]],
) -> int:
    """Store LLM suggestions (merchant_id, clean_name, category_id, confidence).

    Only merchants still unclaimed (source IS NULL) are written, so a user's naming or
    default is never overwritten. Category ids are validated before writing.
    """
    written = 0
    for merchant_id, clean_name, category_id, confidence in rows:
        if category_id is not None and not category_exists(conn, category_id):
            category_id = None
        changed = conn.execute(
            """
            UPDATE merchants
            SET clean_name = ?, default_category_id = ?, confidence = ?, source = 'llm'
            WHERE id = ? AND source IS NULL
            RETURNING id
            """,
            [clean_name, category_id, confidence, merchant_id],
        ).fetchall()
        written += len(changed)
    return written


def recurring_inputs(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Outflows eligible for recurring detection: merchant, date, amount_cents (< 0).

    Transfers, transfer candidates (e.g. an autopay whose card side isn't imported) and
    excluded rows are left out: a card payment is not a subscription.
    """
    cursor = conn.execute(
        """
        SELECT merchant_id, txn_date AS date, amount_cents FROM transactions
        WHERE amount_cents < 0 AND merchant_id IS NOT NULL
          AND NOT is_transfer AND NOT transfer_candidate AND NOT is_excluded
        ORDER BY merchant_id, txn_date
        """
    )
    columns = [d[0] for d in cursor.description]
    return pd.DataFrame(cursor.fetchall(), columns=columns)


def save_recurring_series(conn: duckdb.DuckDBPyConnection, series: pd.DataFrame) -> int:
    """Replace detection results. Columns: merchant_id, cadence_days, typical_cents,
    last_seen, next_expected. Dismissed and cancelled series keep the user's choice;
    series no longer detected are removed unless the user dismissed or cancelled them.
    Returns the number of detected series.
    """
    conn.register("staged_series", series)
    try:
        conn.execute(
            """
            DELETE FROM recurring_series
            WHERE status IS DISTINCT FROM 'dismissed' AND cancelled_on IS NULL
              AND merchant_id NOT IN (SELECT CAST(merchant_id AS INTEGER) FROM staged_series)
            """
        )
        conn.execute(
            """
            INSERT INTO recurring_series
                (merchant_id, cadence_days, typical_cents, last_seen, next_expected, status)
            SELECT CAST(merchant_id AS INTEGER), CAST(cadence_days AS INTEGER),
                   CAST(typical_cents AS BIGINT), CAST(last_seen AS DATE),
                   CAST(next_expected AS DATE), 'active'
            FROM staged_series
            ON CONFLICT (merchant_id) DO UPDATE SET
                cadence_days = excluded.cadence_days,
                typical_cents = excluded.typical_cents,
                last_seen = excluded.last_seen,
                next_expected = excluded.next_expected
            """
        )
    finally:
        conn.unregister("staged_series")
    return len(series)


@dataclass(frozen=True)
class StoredSeries:
    id: int
    merchant: str
    cadence_days: int
    typical_cents: int
    last_seen: date
    next_expected: date
    status: str | None
    cancelled_on: date | None


def list_recurring_series(conn: duckdb.DuckDBPyConnection) -> list[StoredSeries]:
    """Every stored series with its merchant's display name."""
    rows = conn.execute(
        """
        SELECT s.id, COALESCE(m.clean_name, m.normalized_key), s.cadence_days,
               s.typical_cents, s.last_seen, s.next_expected, s.status, s.cancelled_on
        FROM recurring_series s
        JOIN merchants m ON m.id = s.merchant_id
        ORDER BY s.id
        """
    ).fetchall()
    return [
        StoredSeries(int(r[0]), str(r[1]), int(r[2]), int(r[3]), r[4], r[5], r[6], r[7])
        for r in rows
    ]


def latest_transaction_date(conn: duckdb.DuckDBPyConnection) -> date | None:
    """The newest imported transaction date: how far the data reaches."""
    row = conn.execute("SELECT max(txn_date) FROM transactions").fetchone()
    return None if row is None else row[0]


def dismiss_recurring(conn: duckdb.DuckDBPyConnection, series_id: int) -> bool:
    """User: not a subscription. Kept across re-detection. Returns whether it existed."""
    changed = conn.execute(
        "UPDATE recurring_series SET status = 'dismissed' WHERE id = ? RETURNING id",
        [series_id],
    ).fetchall()
    return bool(changed)


def cancel_recurring(conn: duckdb.DuckDBPyConnection, series_id: int, on: date) -> bool:
    """User: cancelled this subscription on `on`. Kept across re-detection."""
    changed = conn.execute(
        "UPDATE recurring_series SET cancelled_on = ? WHERE id = ? RETURNING id",
        [on, series_id],
    ).fetchall()
    return bool(changed)


def restore_recurring(conn: duckdb.DuckDBPyConnection, series_id: int) -> bool:
    """Undo a dismissal or cancellation. Detection may remove it again if the pattern
    no longer holds."""
    changed = conn.execute(
        "UPDATE recurring_series SET status = 'active', cancelled_on = NULL "
        "WHERE id = ? RETURNING id",
        [series_id],
    ).fetchall()
    return bool(changed)


def anomaly_inputs(conn: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    """Outflows eligible for anomaly detection, with display names for explanations:
    (id, account_id, account, merchant_id, merchant, category_id, category, date,
    amount_cents). Same exclusions as recurring detection: transfers, transfer candidates,
    excluded rows."""
    return conn.execute(
        """
        SELECT t.id, t.account_id, a.display_name, t.merchant_id,
               COALESCE(m.clean_name, m.normalized_key), t.category_id, c.name,
               t.txn_date, t.amount_cents
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        JOIN merchants m ON m.id = t.merchant_id
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE t.amount_cents < 0
          AND NOT t.is_transfer AND NOT t.transfer_candidate AND NOT t.is_excluded
        ORDER BY t.txn_date, t.id
        """
    ).fetchall()


def undismissed_series_merchants(conn: duckdb.DuckDBPyConnection) -> set[int]:
    """Merchants with a recurring series the user hasn't dismissed (price-increase check)."""
    rows = conn.execute(
        "SELECT merchant_id FROM recurring_series WHERE status IS DISTINCT FROM 'dismissed'"
    ).fetchall()
    return {int(r[0]) for r in rows}


def save_anomalies(conn: duckdb.DuckDBPyConnection, anomalies: pd.DataFrame) -> int:
    """Replace detection results. Columns: transaction_id, kind, score, explanation.
    Dismissed anomalies keep the user's choice and survive even when no longer detected;
    others that are no longer detected are removed. Returns the number detected."""
    conn.register("staged_anomalies", anomalies)
    try:
        conn.execute(
            """
            DELETE FROM anomalies
            WHERE NOT dismissed
              AND NOT EXISTS (
                  SELECT 1 FROM staged_anomalies s
                  WHERE CAST(s.transaction_id AS TEXT) = anomalies.transaction_id
                    AND CAST(s.kind AS TEXT) = anomalies.kind)
            """
        )
        conn.execute(
            """
            INSERT INTO anomalies (transaction_id, kind, score, explanation, dismissed)
            SELECT CAST(transaction_id AS TEXT), CAST(kind AS TEXT),
                   CAST(score AS DOUBLE), CAST(explanation AS TEXT), FALSE
            FROM staged_anomalies
            ON CONFLICT (transaction_id, kind) DO UPDATE SET
                score = excluded.score,
                explanation = excluded.explanation
            """
        )
    finally:
        conn.unregister("staged_anomalies")
    return len(anomalies)


@dataclass(frozen=True)
class StoredAnomaly:
    transaction_id: str
    kind: str
    date: date
    merchant: str
    account: str
    amount_cents: int
    explanation: str
    dismissed: bool


def list_anomalies(
    conn: duckdb.DuckDBPyConnection,
    start: date,
    end: date,
    *,
    include_dismissed: bool = False,
) -> list[StoredAnomaly]:
    """Anomalies on transactions dated start..end, newest first."""
    rows = conn.execute(
        """
        SELECT an.transaction_id, an.kind, t.txn_date,
               COALESCE(m.clean_name, m.normalized_key, t.raw_description),
               a.display_name, t.amount_cents, an.explanation, an.dismissed
        FROM anomalies an
        JOIN transactions t ON t.id = an.transaction_id
        JOIN accounts a ON a.id = t.account_id
        LEFT JOIN merchants m ON m.id = t.merchant_id
        WHERE t.txn_date BETWEEN ? AND ? AND (? OR NOT an.dismissed)
        ORDER BY t.txn_date DESC, an.kind, an.transaction_id
        """,
        [start, end, include_dismissed],
    ).fetchall()
    return [
        StoredAnomaly(
            str(r[0]), str(r[1]), r[2], str(r[3]), str(r[4]), int(r[5]), str(r[6]), bool(r[7])
        )
        for r in rows
    ]


def set_anomaly_dismissed(
    conn: duckdb.DuckDBPyConnection, transaction_id: str, kind: str, dismissed: bool
) -> bool:
    """Dismiss (or restore) one alert. Returns whether it existed."""
    changed = conn.execute(
        "UPDATE anomalies SET dismissed = ? WHERE transaction_id = ? AND kind = ? RETURNING 1",
        [dismissed, transaction_id, kind],
    ).fetchall()
    return bool(changed)
