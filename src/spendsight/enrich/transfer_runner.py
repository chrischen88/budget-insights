"""Run transfer detection over stored transactions and persist the result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

from spendsight.db import repository
from spendsight.enrich.transfers import DEFAULT_WINDOW_DAYS, TransferRow, detect_transfers


@dataclass(frozen=True)
class TransferRunResult:
    new_pairs: int
    unpaired_candidates: int


def _to_row(raw: dict[str, object]) -> TransferRow:
    txn_date = raw["txn_date"]
    amount_cents = raw["amount_cents"]
    if not isinstance(txn_date, date) or not isinstance(amount_cents, int):
        raise TypeError("unexpected column types from repository.unpaired_transactions")
    return TransferRow(
        id=str(raw["id"]),
        account_id=str(raw["account_id"]),
        account_kind=str(raw["account_kind"]),
        account_last4=None if raw["account_last4"] is None else str(raw["account_last4"]),
        txn_date=txn_date,
        amount_cents=amount_cents,
        raw_description=str(raw["raw_description"]),
        chase_type=None if raw["chase_type"] is None else str(raw["chase_type"]),
    )


def run_transfer_detection(
    conn: duckdb.DuckDBPyConnection, *, window_days: int = DEFAULT_WINDOW_DAYS
) -> TransferRunResult:
    """Pair any not-yet-paired candidates. Safe to re-run: existing pairs are never touched."""
    rows = [_to_row(r) for r in repository.unpaired_transactions(conn)]
    result = detect_transfers(rows, window_days=window_days)
    conn.execute("BEGIN TRANSACTION")
    try:
        repository.mark_transfer_candidates(conn, sorted(result.candidate_ids))
        repository.save_transfer_pairs(conn, [(p.outflow_id, p.inflow_id) for p in result.pairs])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return TransferRunResult(
        new_pairs=len(result.pairs),
        unpaired_candidates=repository.count_unpaired_transfer_candidates(conn),
    )
