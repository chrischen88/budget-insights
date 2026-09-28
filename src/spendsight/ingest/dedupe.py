"""Deterministic transaction IDs (SPEC.md §5.2).

Changing the hash inputs changes every ID and is a migration: flag it before doing it.
"""

from __future__ import annotations

import hashlib
from collections import Counter

import pandas as pd

from spendsight.ingest.models import ParsedTransaction


def transaction_id(account_id: str, txn: ParsedTransaction, occurrence_index: int) -> str:
    key = "|".join(
        [
            account_id,
            txn.txn_date.isoformat(),
            str(txn.amount_cents),
            txn.raw_description,
            str(occurrence_index),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def assign_ids(
    account_id: str, txns: list[ParsedTransaction]
) -> list[tuple[str, ParsedTransaction]]:
    """Pair each row with its ID. `occurrence_index` counts identical earlier rows in the
    same file, so two real same-day charges stay distinct while re-imports collide."""
    seen: Counter[tuple[object, ...]] = Counter()
    out = []
    for txn in txns:
        key = (txn.txn_date, txn.amount_cents, txn.raw_description)
        out.append((transaction_id(account_id, txn, seen[key]), txn))
        seen[key] += 1
    return out


def to_frame(rows: list[tuple[str, ParsedTransaction]]) -> pd.DataFrame:
    """ID'd rows -> the DataFrame shape `db.repository.insert_transactions` stores."""
    return pd.DataFrame(
        {
            "id": [tid for tid, _ in rows],
            "txn_date": [t.txn_date for _, t in rows],
            "post_date": [t.post_date for _, t in rows],
            "amount_cents": pd.Series([t.amount_cents for _, t in rows], dtype="int64"),
            "raw_description": [t.raw_description for _, t in rows],
            "chase_category": [t.chase_category for _, t in rows],
            "chase_type": [t.chase_type for _, t in rows],
            "memo": [t.memo for _, t in rows],
        }
    )
