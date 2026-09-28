"""Run recurring detection over stored transactions and persist the series."""

from __future__ import annotations

import duckdb
import pandas as pd

from spendsight.db import repository
from spendsight.ml.recurring import detect_recurring


def run_recurring_detection(conn: duckdb.DuckDBPyConnection) -> int:
    """Detect series from all stored outflows. Returns how many were detected."""
    series = detect_recurring(repository.recurring_inputs(conn))
    frame = pd.DataFrame(
        {
            "merchant_id": pd.Series([s.merchant_id for s in series], dtype="int64"),
            "cadence_days": pd.Series([s.cadence_days for s in series], dtype="int64"),
            "typical_cents": pd.Series([s.typical_cents for s in series], dtype="int64"),
            "last_seen": [s.last_seen for s in series],
            "next_expected": [s.next_expected for s in series],
        }
    )
    conn.execute("BEGIN TRANSACTION")
    try:
        count = repository.save_recurring_series(conn, frame)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return count
