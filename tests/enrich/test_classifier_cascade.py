"""Cascade step 4 through the real pipeline: labels in the database train the classifier,
which categorizes merchants the user hasn't labeled.

Synthetic card data, every row's Chase category "Shopping" (so Chase alone is no help):
  12 "<brand> PIZZA" and 12 "<brand> MARKET" merchants, 5 charges each  (labelable)
  NEWCO PIZZA x2, OTHERCO MARKET x2                                     (never labeled)
"""

from datetime import date, timedelta

import duckdb
import pytest

from spendsight.db import repository
from spendsight.enrich.categorize_runner import run_categorization
from spendsight.enrich.recategorize import recategorize
from spendsight.pipeline import import_and_process
from tests.synth import CARD_HEADER, card_line

BRANDS = [
    "ZOKA", "MIRU", "PELO", "TAVI", "RENO", "DUSA",
    "KIBO", "LOMA", "SEFU", "NARI", "BOTE", "FINA",
]  # fmt: skip
UNLABELED = ("NEWCO PIZZA", "OTHERCO MARKET")


def _csv() -> bytes:
    lines = [CARD_HEADER]
    day = date(2025, 1, 1)
    names = [f"{b} PIZZA" for b in BRANDS] + [f"{b} MARKET" for b in BRANDS]
    for i, name in enumerate(names):
        amount = "-31.00" if "PIZZA" in name else "-84.00"
        for k in range(5):
            lines.append(card_line(day + timedelta(days=i + 7 * k), name, amount, "Shopping"))
    lines.append(card_line(date(2025, 3, 1), "NEWCO PIZZA", "-29.00", "Shopping"))
    lines.append(card_line(date(2025, 3, 8), "NEWCO PIZZA", "-33.00", "Shopping"))
    lines.append(card_line(date(2025, 3, 2), "OTHERCO MARKET", "-79.00", "Shopping"))
    lines.append(card_line(date(2025, 3, 9), "OTHERCO MARKET", "-91.00", "Shopping"))
    lines.append(card_line(date(2025, 3, 20), "EXTRA MARKET", "-66.00", "Shopping"))
    return "".join(lines).encode()


@pytest.fixture
def loaded(db: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    import_and_process(db, _csv(), filename="card.csv", last4="0000")
    return db


def _cat(db: duckdb.DuckDBPyConnection, name: str) -> int:
    row = db.execute("SELECT id FROM categories WHERE name = ?", [name]).fetchone()
    assert row is not None
    return int(row[0])


def _merchant(db: duckdb.DuckDBPyConnection, key: str) -> int:
    row = db.execute("SELECT id FROM merchants WHERE normalized_key = ?", [key]).fetchone()
    assert row is not None, key
    return int(row[0])


def _label_merchants(db: duckdb.DuckDBPyConnection, count: int) -> None:
    """User merchant defaults for the first `count` brands of each kind (5 rows each)."""
    for brand in BRANDS[:count]:
        repository.set_merchant_default(
            db, _merchant(db, f"{brand} PIZZA"), _cat(db, "Restaurants")
        )
        repository.set_merchant_default(db, _merchant(db, f"{brand} MARKET"), _cat(db, "Groceries"))


def _state(db: duckdb.DuckDBPyConnection, key: str) -> set[tuple[str | None, str | None]]:
    rows = db.execute(
        "SELECT c.name, t.category_source FROM transactions t "
        "JOIN merchants m ON m.id = t.merchant_id "
        "LEFT JOIN categories c ON c.id = t.category_id WHERE m.normalized_key = ?",
        [key],
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def test_off_below_100_labels(loaded: duckdb.DuckDBPyConnection) -> None:
    _label_merchants(loaded, 9)  # 90 labels
    run_categorization(loaded)
    assert _state(loaded, "NEWCO PIZZA") == {("Shopping", "chase")}
    assert loaded.execute(
        "SELECT count(*) FROM transactions WHERE category_source = 'ml'"
    ).fetchone() == (0,)


def test_learns_unlabeled_merchants(loaded: duckdb.DuckDBPyConnection) -> None:
    _label_merchants(loaded, 12)  # 120 labels
    run_categorization(loaded)
    assert _state(loaded, "NEWCO PIZZA") == {("Restaurants", "ml")}
    assert _state(loaded, "OTHERCO MARKET") == {("Groceries", "ml")}
    confs = loaded.execute(
        "SELECT min(category_conf), max(category_conf) FROM transactions "
        "WHERE category_source = 'ml'"
    ).fetchone()
    assert confs is not None
    assert 0.75 <= confs[0] <= confs[1] <= 1.0
    # Labeled merchants keep their user default; ml never overrides it.
    assert _state(loaded, "ZOKA PIZZA") == {("Restaurants", "user")}


def test_own_output_is_not_training_data(loaded: duckdb.DuckDBPyConnection) -> None:
    """A second run sees 'ml' rows in the database but must train on the same labels,
    so nothing changes."""
    _label_merchants(loaded, 12)
    run_categorization(loaded)
    assert run_categorization(loaded) == 0


def test_threshold_is_respected(loaded: duckdb.DuckDBPyConnection) -> None:
    _label_merchants(loaded, 12)
    run_categorization(loaded, ml_threshold=1.0)
    assert _state(loaded, "NEWCO PIZZA") == {("Shopping", "chase")}


def test_locked_edits_are_labels(loaded: duckdb.DuckDBPyConnection) -> None:
    """No merchant defaults at all: 120 per-transaction edits (not re-run individually)."""
    restaurants, groceries = _cat(loaded, "Restaurants"), _cat(loaded, "Groceries")
    for brand in BRANDS:
        for kind, category in (("PIZZA", restaurants), ("MARKET", groceries)):
            ids = repository.merchant_transaction_ids(loaded, _merchant(loaded, f"{brand} {kind}"))
            for txn in ids:
                repository.set_transaction_category(loaded, txn, category)
    run_categorization(loaded)
    assert _state(loaded, "NEWCO PIZZA") == {("Restaurants", "ml")}


def test_edit_that_turns_learning_on_reports_relearned_rows(
    loaded: duckdb.DuckDBPyConnection,
) -> None:
    """95 merchant-default labels + 4 locked rows = 99. One more edit makes 100: the
    classifier switches on and moves the four NEWCO/OTHERCO rows (and nothing it was told)."""
    _label_merchants(loaded, 9)  # 90
    groceries = _cat(loaded, "Groceries")
    repository.set_merchant_default(
        loaded, _merchant(loaded, "NARI PIZZA"), _cat(loaded, "Restaurants")
    )
    for txn in repository.merchant_transaction_ids(loaded, _merchant(loaded, "NARI MARKET"))[:4]:
        repository.set_transaction_category(loaded, txn, groceries)
    run_categorization(loaded)
    assert _state(loaded, "NEWCO PIZZA") == {("Shopping", "chase")}

    [extra] = repository.merchant_transaction_ids(loaded, _merchant(loaded, "EXTRA MARKET"))
    result = recategorize(loaded, extra, groceries)
    assert _state(loaded, "NEWCO PIZZA") == {("Restaurants", "ml")}
    assert result.relearned >= 4  # NEWCO x2, OTHERCO x2, plus any unlabeled brand rows
