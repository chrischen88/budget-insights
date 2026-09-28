"""Run the categorization cascade over stored transactions and persist the result.

Two passes: the first works out this run's rule and user-default assignments, which
(with locked per-transaction edits) are the classifier's training labels; the classifier
is retrained from them and its suggestions fill cascade step 4 in the second pass.
Retraining every run is cheap (logistic regression) and means no stored model to go stale.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from spendsight.db import repository
from spendsight.enrich.categorize import (
    DEFAULT_ML_THRESHOLD,
    USER_MERCHANT_CONF,
    CascadeRow,
    CategoryAssignment,
    MerchantDefault,
    MlSuggestion,
    categorize,
    compile_rules,
)
from spendsight.ml.classifier import Example, LabeledExample, train

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


LABEL_SOURCES = ("rule", "user")  # never "ml": the model doesn't learn from itself


def _example(row: repository.ClassifierRow) -> Example:
    return Example(row.key, row.amount_cents, row.chase_category)


def ml_suggestions(
    conn: duckdb.DuckDBPyConnection, first_pass: list[CategoryAssignment]
) -> dict[str, MlSuggestion]:
    """Train on locked edits + this run's rule/user assignments, then suggest a category
    for every row those didn't decide. Empty below the label minimum."""
    info = {r.id: r for r in repository.classifier_rows(conn)}
    labels = {r.id: r.category_id for r in info.values() if r.locked and r.category_id}
    labels.update(
        {
            a.transaction_id: a.category_id
            for a in first_pass
            if a.source in LABEL_SOURCES and a.category_id is not None
        }
    )
    model = train([LabeledExample(_example(info[t]), c) for t, c in sorted(labels.items())])
    if model is None:
        return {}
    todo = [a.transaction_id for a in first_pass if a.source not in LABEL_SOURCES]
    predictions = model.predict([_example(info[t]) for t in todo])
    return {t: MlSuggestion(p.category_id, p.conf) for t, p in zip(todo, predictions, strict=True)}


def run_categorization(
    conn: duckdb.DuckDBPyConnection, *, ml_threshold: float = DEFAULT_ML_THRESHOLD
) -> int:
    """Categorize every unlocked transaction. Returns how many changed.

    Safe to re-run at any time; re-running after rules, merchant defaults, the Chase
    mapping or any user edit (new training labels) brings every unlocked row up to date.
    """
    rows = [
        CascadeRow(txn_id, desc, merchant_id, chase)
        for txn_id, desc, merchant_id, chase in repository.rows_for_categorization(conn)
    ]
    rules = compile_rules(repository.category_rules(conn))
    defaults = _merchant_defaults(conn)
    chase = repository.chase_category_mapping(conn)
    assignments = categorize(rows, rules=rules, merchant_defaults=defaults, chase_mapping=chase)
    suggestions = ml_suggestions(conn, assignments)
    if suggestions:
        assignments = categorize(
            rows,
            rules=rules,
            merchant_defaults=defaults,
            chase_mapping=chase,
            ml_suggestions=suggestions,
            ml_threshold=ml_threshold,
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
