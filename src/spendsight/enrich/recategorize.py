"""User recategorization (SPEC.md §5.5 learning loop).

Editing a transaction locks it to the chosen category. "Apply to merchant" also sets the
merchant's default, then re-runs the cascade so the merchant's other rows follow, except
where a rule or another override takes precedence (rules outrank merchant defaults).
The locked, user-labeled rows are also the classifier's training data (Phase 3).
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from spendsight.db import repository
from spendsight.enrich.categorize_runner import run_categorization


@dataclass(frozen=True)
class RecategorizeResult:
    applied_to_merchant: bool
    merchant_rows: int = 0  # all of the merchant's transactions
    following: int = 0  # now in the chosen category
    held_by_override: int = 0  # locked to a different category by an earlier edit
    held_by_rule: int = 0  # a rule puts them in a different category


def recategorize(
    conn: duckdb.DuckDBPyConnection,
    transaction_id: str,
    category_id: int,
    *,
    apply_to_merchant: bool = False,
) -> RecategorizeResult:
    merchant_id = repository.transaction_merchant(conn, transaction_id)
    repository.set_transaction_category(conn, transaction_id, category_id)
    if not apply_to_merchant or merchant_id is None:
        return RecategorizeResult(applied_to_merchant=False)
    repository.set_merchant_default(conn, merchant_id, category_id)
    run_categorization(conn)
    total, following, locked, ruled = repository.merchant_category_breakdown(
        conn, merchant_id, category_id
    )
    return RecategorizeResult(True, total, following, locked, ruled)


def reset_to_automatic(conn: duckdb.DuckDBPyConnection, transaction_id: str) -> bool:
    """Drop a per-transaction override and let the cascade decide again."""
    if not repository.clear_transaction_override(conn, transaction_id):
        return False
    run_categorization(conn)
    return True
