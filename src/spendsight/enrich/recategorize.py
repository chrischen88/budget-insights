"""User recategorization (SPEC.md §5.5 learning loop).

Editing a transaction locks it to the chosen category. "Apply to merchant" also sets the
merchant's default, then re-runs the cascade so the merchant's other rows follow, except
where a rule or another override takes precedence (rules outrank merchant defaults).
The locked, user-labeled rows are also the classifier's training data, so every edit
re-runs the cascade (retraining it) and reports how many other rows moved as a result.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from spendsight.db import repository
from spendsight.enrich.categorize import DEFAULT_ML_THRESHOLD
from spendsight.enrich.categorize_runner import run_categorization


@dataclass(frozen=True)
class RecategorizeResult:
    applied_to_merchant: bool
    merchant_rows: int = 0  # all of the merchant's transactions
    following: int = 0  # now in the chosen category
    held_by_override: int = 0  # locked to a different category by an earlier edit
    held_by_rule: int = 0  # a rule puts them in a different category
    relearned: int = 0  # other rows the retrained classifier moved (outside this merchant)


def recategorize(
    conn: duckdb.DuckDBPyConnection,
    transaction_id: str,
    category_id: int,
    *,
    apply_to_merchant: bool = False,
    ml_threshold: float = DEFAULT_ML_THRESHOLD,
) -> RecategorizeResult:
    merchant_id = repository.transaction_merchant(conn, transaction_id)
    repository.set_transaction_category(conn, transaction_id, category_id)
    target = merchant_id if apply_to_merchant else None
    if target is not None:
        repository.set_merchant_default(conn, target, category_id)
    before = repository.category_snapshot(conn)
    run_categorization(conn, ml_threshold=ml_threshold)
    after = repository.category_snapshot(conn)
    expected = {transaction_id}
    if target is not None:
        expected.update(repository.merchant_transaction_ids(conn, target))
    relearned = sum(
        1 for txn, category in after.items() if category != before.get(txn) and txn not in expected
    )
    if target is None:
        return RecategorizeResult(applied_to_merchant=False, relearned=relearned)
    total, following, locked, ruled = repository.merchant_category_breakdown(
        conn, target, category_id
    )
    return RecategorizeResult(True, total, following, locked, ruled, relearned)


def reset_to_automatic(
    conn: duckdb.DuckDBPyConnection,
    transaction_id: str,
    *,
    ml_threshold: float = DEFAULT_ML_THRESHOLD,
) -> bool:
    """Drop a per-transaction override and let the cascade decide again."""
    if not repository.clear_transaction_override(conn, transaction_id):
        return False
    run_categorization(conn, ml_threshold=ml_threshold)
    return True
