"""Run anomaly detection over stored transactions and persist the results.

Runs after every import, and again whenever a user change feeds a check: recategorizing
(category outliers) or dismissing a recurring series (price increases).
"""

from __future__ import annotations

import duckdb
import pandas as pd

from spendsight.db import repository
from spendsight.ml.anomalies import Charge, detect_anomalies


def run_anomaly_detection(conn: duckdb.DuckDBPyConnection) -> int:
    """Detect anomalies across all stored outflows. Returns how many were detected."""
    charges = [
        Charge(
            id=str(r[0]),
            account_id=str(r[1]),
            account=str(r[2]),
            merchant_id=int(r[3]),
            merchant=str(r[4]),
            category_id=None if r[5] is None else int(r[5]),
            category=None if r[6] is None else str(r[6]),
            date=r[7],
            amount_cents=int(r[8]),
        )
        for r in repository.anomaly_inputs(conn)
    ]
    found = detect_anomalies(charges, repository.undismissed_series_merchants(conn))
    frame = pd.DataFrame(
        {
            "transaction_id": pd.Series([a.transaction_id for a in found], dtype="object"),
            "kind": pd.Series([a.kind for a in found], dtype="object"),
            "score": pd.Series([a.score for a in found], dtype="float64"),
            "explanation": pd.Series([a.explanation for a in found], dtype="object"),
        }
    )
    conn.execute("BEGIN TRANSACTION")
    try:
        count = repository.save_anomalies(conn, frame)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return count
