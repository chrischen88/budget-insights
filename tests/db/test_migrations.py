from datetime import date
from pathlib import Path

import duckdb
import pytest

from spendsight.db.connection import connect, load_migrations, migrate

EXPECTED_TABLES = {
    "accounts",
    "imports",
    "transactions",
    "merchants",
    "categories",
    "category_rules",
    "recurring_series",
    "anomalies",
    "llm_cache",
    "chase_category_mappings",
    "llm_calls",
    "schema_migrations",
}


def test_migrations_are_numbered_contiguously() -> None:
    versions = [m.version for m in load_migrations()]
    assert versions == list(range(1, len(versions) + 1))


def test_schema_created(db: duckdb.DuckDBPyConnection) -> None:
    tables = {r[0] for r in db.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    assert tables == EXPECTED_TABLES
    views = {
        r[0]
        for r in db.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()
    }
    assert "v_spend" in views


def test_migrate_is_idempotent(db: duckdb.DuckDBPyConnection) -> None:
    assert migrate(db) == []


def test_file_db_persists_migrations(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "test.duckdb"
    connect(path).close()
    conn = connect(path)
    assert migrate(conn) == []
    conn.close()


def test_money_column_is_bigint(db: duckdb.DuckDBPyConnection) -> None:
    (dtype,) = db.execute(
        "SELECT data_type FROM duckdb_columns() "
        "WHERE table_name = 'transactions' AND column_name = 'amount_cents'"
    ).fetchone() or (None,)
    assert dtype == "BIGINT"


def test_seed_taxonomy(db: duckdb.DuckDBPyConnection) -> None:
    parents, children = db.execute(
        "SELECT count(*) FILTER (parent_id IS NULL), count(*) FILTER (parent_id IS NOT NULL) "
        "FROM categories"
    ).fetchone() or (0, 0)
    assert 10 <= parents <= 16
    assert 35 <= children <= 50
    # Income children inherit is_income from their parent.
    non_income = db.execute(
        "SELECT count(*) FROM categories c JOIN categories p ON p.id = c.parent_id "
        "WHERE p.name = 'Income' AND NOT c.is_income"
    ).fetchone()
    assert non_income == (0,)


def _add_account(db: duckdb.DuckDBPyConnection) -> None:
    db.execute(
        "INSERT INTO accounts VALUES ('chase-card-0000', 'credit_card', 'Test Card', '0000')"
    )


def _add_txn(
    db: duckdb.DuckDBPyConnection,
    txn_id: str,
    cents: int,
    *,
    is_transfer: bool = False,
    is_excluded: bool = False,
) -> None:
    db.execute(
        "INSERT INTO transactions (id, account_id, txn_date, amount_cents, raw_description, "
        "is_transfer, is_excluded) VALUES (?, 'chase-card-0000', ?, ?, 'SYNTHETIC MERCHANT', ?, ?)",
        [txn_id, date(2026, 1, 15), cents, is_transfer, is_excluded],
    )


def test_v_spend_excludes_transfers_and_excluded(db: duckdb.DuckDBPyConnection) -> None:
    _add_account(db)
    _add_txn(db, "a", -1234)
    _add_txn(db, "b", -500)
    _add_txn(db, "c", 50000, is_transfer=True)
    _add_txn(db, "d", -999, is_excluded=True)
    assert db.execute("SELECT sum(amount_cents) FROM v_spend").fetchone() == (-1734,)


def test_category_requires_provenance(db: duckdb.DuckDBPyConnection) -> None:
    _add_account(db)
    _add_txn(db, "a", -100)
    with pytest.raises(duckdb.ConstraintException):
        db.execute("UPDATE transactions SET category_id = 1 WHERE id = 'a'")
    db.execute(
        "UPDATE transactions SET category_id = 1, category_source = 'rule', category_conf = 1.0 "
        "WHERE id = 'a'"
    )


def test_last4_must_be_four_digits(db: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(duckdb.ConstraintException):
        db.execute("INSERT INTO accounts VALUES ('x', 'checking', 'X', '123456789')")
