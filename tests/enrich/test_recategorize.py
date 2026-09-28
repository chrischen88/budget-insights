import duckdb
import pytest

from spendsight.enrich.recategorize import RecategorizeResult, recategorize, reset_to_automatic
from spendsight.pipeline import import_and_process
from tests.conftest import fixture_bytes


@pytest.fixture
def loaded(db: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    import_and_process(db, fixture_bytes("chase_card_overlap.csv"), filename="o.csv", last4="0000")
    return db


def _cat(db: duckdb.DuckDBPyConnection, name: str) -> int:
    row = db.execute("SELECT id FROM categories WHERE name = ?", [name]).fetchone()
    assert row is not None
    return int(row[0])


def _coffee_ids(db: duckdb.DuckDBPyConnection) -> list[str]:
    rows = db.execute(
        "SELECT id FROM transactions WHERE raw_description = 'SYNTH COFFEE CO' ORDER BY id"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _coffee_state(db: duckdb.DuckDBPyConnection) -> list[tuple[object, ...]]:
    return db.execute(
        "SELECT t.id, c.name, t.category_source, t.category_locked FROM transactions t "
        "LEFT JOIN categories c ON c.id = t.category_id "
        "WHERE t.raw_description = 'SYNTH COFFEE CO' ORDER BY t.id"
    ).fetchall()


def test_single_edit_locks_only_that_row(loaded: duckdb.DuckDBPyConnection) -> None:
    first, *others = _coffee_ids(loaded)
    result = recategorize(loaded, first, _cat(loaded, "Coffee Shops"))
    assert result == RecategorizeResult(applied_to_merchant=False)
    state = {r[0]: r[1:] for r in _coffee_state(loaded)}
    assert state[first] == ("Coffee Shops", "user", True)
    for other in others:
        assert state[other] == ("Food & Dining", "chase", False)


def test_apply_to_merchant_moves_every_row(loaded: duckdb.DuckDBPyConnection) -> None:
    first, *_ = _coffee_ids(loaded)
    result = recategorize(loaded, first, _cat(loaded, "Coffee Shops"), apply_to_merchant=True)
    assert result == RecategorizeResult(True, merchant_rows=3, following=3)
    assert [r[1] for r in _coffee_state(loaded)] == ["Coffee Shops"] * 3
    # Only the edited row is locked; the others follow the merchant default.
    assert sorted(r[2:] for r in _coffee_state(loaded)) == [
        ("user", False),
        ("user", False),
        ("user", True),
    ]


def test_rule_takes_precedence_over_merchant_default(loaded: duckdb.DuckDBPyConnection) -> None:
    loaded.execute(
        "INSERT INTO category_rules (pattern, category_id, priority, created_by) "
        "VALUES ('SYNTH COFFEE', ?, 50, 'user')",
        [_cat(loaded, "Restaurants")],
    )
    first, *_ = _coffee_ids(loaded)
    result = recategorize(loaded, first, _cat(loaded, "Coffee Shops"), apply_to_merchant=True)
    assert result == RecategorizeResult(True, merchant_rows=3, following=1, held_by_rule=2)


def test_earlier_override_is_kept(loaded: duckdb.DuckDBPyConnection) -> None:
    first, second, _third = _coffee_ids(loaded)
    recategorize(loaded, second, _cat(loaded, "Gifts"))
    result = recategorize(loaded, first, _cat(loaded, "Coffee Shops"), apply_to_merchant=True)
    assert result == RecategorizeResult(True, merchant_rows=3, following=2, held_by_override=1)
    assert {r[0]: r[1] for r in _coffee_state(loaded)}[second] == "Gifts"


def test_reset_returns_to_cascade(loaded: duckdb.DuckDBPyConnection) -> None:
    first, *_ = _coffee_ids(loaded)
    recategorize(loaded, first, _cat(loaded, "Gifts"))
    assert reset_to_automatic(loaded, first)
    assert {r[0]: r[1:] for r in _coffee_state(loaded)}[first] == ("Food & Dining", "chase", False)
    assert not reset_to_automatic(loaded, first)  # nothing to reset now


def test_reset_follows_merchant_default(loaded: duckdb.DuckDBPyConnection) -> None:
    first, second, _ = _coffee_ids(loaded)
    recategorize(loaded, second, _cat(loaded, "Gifts"))
    recategorize(loaded, first, _cat(loaded, "Coffee Shops"), apply_to_merchant=True)
    reset_to_automatic(loaded, second)
    assert {r[0]: r[1:] for r in _coffee_state(loaded)}[second] == ("Coffee Shops", "user", False)


def test_unknown_category_changes_nothing(loaded: duckdb.DuckDBPyConnection) -> None:
    before = _coffee_state(loaded)
    with pytest.raises(ValueError, match="unknown category"):
        recategorize(loaded, _coffee_ids(loaded)[0], 99999, apply_to_merchant=True)
    assert _coffee_state(loaded) == before


def test_unknown_transaction(loaded: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="unknown transaction"):
        recategorize(loaded, "missing", _cat(loaded, "Gifts"))
