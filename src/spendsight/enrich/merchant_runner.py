"""Link stored transactions to merchants by their deterministic key."""

from __future__ import annotations

import duckdb
import pandas as pd

from spendsight.db import repository
from spendsight.enrich.merchants import normalize_key


def run_merchant_linking(conn: duckdb.DuckDBPyConnection) -> int:
    """Key and link every transaction without a merchant. Returns how many were linked."""
    rows = repository.transactions_without_merchant(conn)
    links = pd.DataFrame(
        {
            "transaction_id": [txn_id for txn_id, _ in rows],
            "normalized_key": [normalize_key(desc) for _, desc in rows],
        }
    )
    conn.execute("BEGIN TRANSACTION")
    try:
        linked = repository.link_merchants(conn, links)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return linked
