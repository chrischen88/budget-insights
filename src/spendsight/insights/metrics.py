"""Spending metrics for the Overview page (SPEC.md §9). All amounts are integer cents.

Spend/income classification comes from the `flow` column of the v_spend views, so every
metric here agrees on what counts as spending.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

import duckdb
import pandas as pd

UNCATEGORIZED = "Uncategorized"


@dataclass(frozen=True)
class SpendFilter:
    start: date
    end: date
    account_ids: tuple[str, ...] | None = None  # None = all accounts
    include_transfers: bool = False

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("end must not be before start")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def prior(self) -> SpendFilter:
        """The same-length window immediately before this one."""
        end = self.start - timedelta(days=1)
        return replace(self, start=end - timedelta(days=self.days - 1), end=end)


@dataclass(frozen=True)
class Kpis:
    spent_cents: int
    income_cents: int

    @property
    def net_cents(self) -> int:
        return self.income_cents - self.spent_cents


def _source(f: SpendFilter) -> tuple[str, list[object]]:
    """FROM + WHERE for a filter. The view name comes from a fixed pair, never user input."""
    view = "v_spend_with_transfers" if f.include_transfers else "v_spend"
    sql = f"FROM {view} WHERE date BETWEEN ? AND ?"
    params: list[object] = [f.start, f.end]
    if f.account_ids is not None:
        sql += " AND account_id IN (SELECT unnest(?::TEXT[]))"
        params.append(list(f.account_ids))
    return sql, params


def _frame(conn: duckdb.DuckDBPyConnection, sql: str, params: list[object]) -> pd.DataFrame:
    cursor = conn.execute(sql, params)
    columns = [d[0] for d in cursor.description]
    return pd.DataFrame(cursor.fetchall(), columns=columns)


def date_bounds(conn: duckdb.DuckDBPyConnection) -> tuple[date, date] | None:
    row = conn.execute("SELECT min(date), max(date) FROM v_spend_with_transfers").fetchone()
    if row is None or row[0] is None:
        return None
    return row[0], row[1]


def has_transactions(conn: duckdb.DuckDBPyConnection, f: SpendFilter) -> bool:
    source, params = _source(f)
    row = conn.execute(f"SELECT EXISTS (SELECT 1 {source})", params).fetchone()
    return bool(row and row[0])


def kpis(conn: duckdb.DuckDBPyConnection, f: SpendFilter) -> Kpis:
    source, params = _source(f)
    row = conn.execute(
        f"""
        SELECT
            COALESCE(sum(-amount_cents) FILTER (flow = 'spend'), 0),
            COALESCE(sum(amount_cents) FILTER (flow = 'income'), 0)
        {source}
        """,
        params,
    ).fetchone()
    assert row is not None
    return Kpis(spent_cents=int(row[0]), income_cents=int(row[1]))


def monthly_spend_by_category(conn: duckdb.DuckDBPyConnection, f: SpendFilter) -> pd.DataFrame:
    """Columns: month (first day), parent_category, spend_cents."""
    source, params = _source(f)
    return _frame(
        conn,
        f"""
        SELECT CAST(date_trunc('month', date) AS DATE) AS month,
               COALESCE(parent_category, '{UNCATEGORIZED}') AS parent_category,
               CAST(sum(-amount_cents) AS BIGINT) AS spend_cents
        {source} AND flow = 'spend'
        GROUP BY ALL
        ORDER BY month, parent_category
        """,
        params,
    )


def spend_by_category(conn: duckdb.DuckDBPyConnection, f: SpendFilter) -> pd.DataFrame:
    """Columns: parent_category, spend_cents; largest first."""
    source, params = _source(f)
    return _frame(
        conn,
        f"""
        SELECT COALESCE(parent_category, '{UNCATEGORIZED}') AS parent_category,
               CAST(sum(-amount_cents) AS BIGINT) AS spend_cents
        {source} AND flow = 'spend'
        GROUP BY ALL
        ORDER BY spend_cents DESC, parent_category
        """,
        params,
    )


def top_merchants(
    conn: duckdb.DuckDBPyConnection, f: SpendFilter, *, limit: int = 10
) -> pd.DataFrame:
    """Columns: merchant, spend_cents, transactions. Merchants with net spend > 0 only."""
    source, params = _source(f)
    return _frame(
        conn,
        f"""
        SELECT merchant,
               CAST(sum(-amount_cents) AS BIGINT) AS spend_cents,
               CAST(count(*) AS BIGINT) AS transactions
        {source} AND flow = 'spend'
        GROUP BY ALL
        HAVING sum(-amount_cents) > 0
        ORDER BY spend_cents DESC, merchant
        LIMIT ?
        """,
        [*params, limit],
    )
