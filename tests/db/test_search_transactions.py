from datetime import date

import duckdb
import pytest

from spendsight.db.repository import TransactionQuery, list_categories, search_transactions
from spendsight.pipeline import import_and_process
from tests.conftest import fixture_bytes


@pytest.fixture
def loaded(db: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    import_and_process(db, fixture_bytes("chase_checking_jan.csv"), filename="k.csv", last4="0000")
    return db


def _descriptions(db: duckdb.DuckDBPyConnection, q: TransactionQuery) -> list[str]:
    frame, _ = search_transactions(db, q)
    return list(frame["raw_description"])


def _cat(db: duckdb.DuckDBPyConnection, name: str) -> int:
    return next(c.id for c in list_categories(db) if c.name == name)


def test_default_excludes_transfers_newest_first(loaded: duckdb.DuckDBPyConnection) -> None:
    frame, total = search_transactions(loaded, TransactionQuery())
    assert total == len(frame) == 14  # 16 rows minus the paired $500 transfer
    assert list(frame["date"]) == sorted(frame["date"], reverse=True)
    _, total_all = search_transactions(loaded, TransactionQuery(include_transfers=True))
    assert total_all == 16


def test_text_search_is_case_insensitive(loaded: duckdb.DuckDBPyConnection) -> None:
    assert _descriptions(loaded, TransactionQuery(text="coffee")) == ["SYNTH COFFEE CO"] * 2
    # Matches the cleaned merchant key too: "SYNTH GROCER" is the key for "SYNTH GROCER #123".
    assert _descriptions(loaded, TransactionQuery(text="synth grocer")) == ["SYNTH GROCER #123"]
    assert _descriptions(loaded, TransactionQuery(text="%")) == []  # no wildcard semantics


def test_text_search_matches_memo(loaded: duckdb.DuckDBPyConnection) -> None:
    assert _descriptions(loaded, TransactionQuery(text="check #1001")) == ["CHECK 1001"]


def test_parent_category_includes_children(loaded: duckdb.DuckDBPyConnection) -> None:
    # Food & Dining = 2 coffees (mapped to the parent) + grocer (child: Groceries).
    found = _descriptions(loaded, TransactionQuery(category_id=_cat(loaded, "Food & Dining")))
    assert sorted(found) == ["SYNTH COFFEE CO", "SYNTH COFFEE CO", "SYNTH GROCER #123"]
    assert _descriptions(loaded, TransactionQuery(category_id=_cat(loaded, "Groceries"))) == [
        "SYNTH GROCER #123"
    ]


def test_uncategorized_only(loaded: duckdb.DuckDBPyConnection) -> None:
    assert sorted(_descriptions(loaded, TransactionQuery(uncategorized_only=True))) == [
        "CHECK 1001",
        "SYNTH MARKET PURCHASE",
        "SYNTH RENT CO WEB PMTS",
        "SYNTH SHOP, INC",
    ]


def test_account_and_date_filters(loaded: duckdb.DuckDBPyConnection) -> None:
    q = TransactionQuery(
        start=date(2026, 1, 1), end=date(2026, 1, 5), account_ids=("chase-checking-0000",)
    )
    assert _descriptions(loaded, q) == ["SYNTH RENT CO WEB PMTS", "SYNTH EMPLOYER PAYROLL"]


def test_limit_reports_total(loaded: duckdb.DuckDBPyConnection) -> None:
    frame, total = search_transactions(loaded, TransactionQuery(limit=3))
    assert (len(frame), total) == (3, 14)


def test_no_matches(loaded: duckdb.DuckDBPyConnection) -> None:
    frame, total = search_transactions(loaded, TransactionQuery(text="nothing like this"))
    assert frame.empty
    assert total == 0


def test_category_labels(db: duckdb.DuckDBPyConnection) -> None:
    labels = [c.label for c in list_categories(db)]
    assert "Food & Dining / Coffee Shops" in labels
    assert "Food & Dining" in labels
    # Each parent comes right before its children.
    i = labels.index("Food & Dining")
    assert labels[i + 1].startswith("Food & Dining / ")
