"""Run the categorization cascade over stored transactions and persist the result."""

from __future__ import annotations

import duckdb
import pandas as pd

from spendsight.db import repository
from spendsight.enrich.categorize import (
    USER_MERCHANT_CONF,
    CascadeRow,
    MerchantDefault,
    categorize,
    compile_rules,
)

# Merchant defaults suggested by the LLM without a stored confidence get this one.
LLM_MERCHANT_DEFAULT_CONF = 0.7


def _merchant_defaults(conn: duckdb.DuckDBPyConnection) -> dict[int, MerchantDefault]:
    defaults = {}
    for merchant_id, category_id, source, confidence in repository.merchant_defaults(conn):
        if source == "user":
            defaults[merchant_id] = MerchantDefault(category_id, "user", USER_MERCHANT_CONF)
        elif source == "llm":
            conf: float = LLM_MERCHANT_DEFAULT_CONF if confidence is None else confidence
            defaults[merchant_id] = MerchantDefault(category_id, "llm", conf)
    return defaults


def run_categorization(conn: duckdb.DuckDBPyConnection) -> int:
    """Categorize every unlocked transaction. Returns how many changed.

    Safe to re-run at any time; re-running after rules, merchant defaults, or the Chase
    mapping change brings every unlocked row up to date.
    """
    rows = [
        CascadeRow(txn_id, desc, merchant_id, chase)
        for txn_id, desc, merchant_id, chase in repository.rows_for_categorization(conn)
    ]
    assignments = categorize(
        rows,
        rules=compile_rules(repository.category_rules(conn)),
        merchant_defaults=_merchant_defaults(conn),
        chase_mapping=repository.chase_category_mapping(conn),
    )
    frame = pd.DataFrame(
        {
            "transaction_id": [a.transaction_id for a in assignments],
            "category_id": pd.Series([a.category_id for a in assignments], dtype="Int64"),
            "source": [a.source for a in assignments],
            "conf": pd.Series([a.conf for a in assignments], dtype="Float64"),
        }
    )
    conn.execute("BEGIN TRANSACTION")
    try:
        changed = repository.save_category_assignments(conn, frame)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return changed
