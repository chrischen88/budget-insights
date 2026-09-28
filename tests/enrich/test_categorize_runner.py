import duckdb
import pytest

from spendsight.db import repository
from spendsight.enrich.categorize import CHASE_FALLBACK_CONF, RULE_CONF
from spendsight.enrich.categorize_runner import run_categorization
from spendsight.pipeline import import_and_process
from tests.conftest import fixture_bytes


@pytest.fixture
def loaded(db: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    import_and_process(db, fixture_bytes("chase_checking_jan.csv"), filename="k.csv", last4="0000")
    return db


def _category_of(db: duckdb.DuckDBPyConnection, description: str) -> list[tuple[object, ...]]:
    return db.execute(
        "SELECT c.name, t.category_source, t.category_conf FROM transactions t "
        "LEFT JOIN categories c ON c.id = t.category_id "
        "WHERE t.raw_description = ? ORDER BY t.id",
        [description],
    ).fetchall()


def _category_id(db: duckdb.DuckDBPyConnection, name: str) -> int:
    row = db.execute("SELECT id FROM categories WHERE name = ?", [name]).fetchone()
    assert row is not None
    return int(row[0])


def _merchant_id(db: duckdb.DuckDBPyConnection, key: str) -> int:
    row = db.execute("SELECT id FROM merchants WHERE normalized_key = ?", [key]).fetchone()
    assert row is not None
    return int(row[0])


def test_seed_mapping_and_rules_exist(db: duckdb.DuckDBPyConnection) -> None:
    assert db.execute("SELECT count(*) FROM chase_category_mappings").fetchone() == (14,)
    assert db.execute(
        "SELECT count(*) FROM category_rules WHERE created_by = 'seed'"
    ).fetchone() == (11,)
    orphans = db.execute(
        "SELECT count(*) FROM category_rules r LEFT JOIN categories c ON c.id = r.category_id "
        "WHERE c.id IS NULL"
    ).fetchone()
    assert orphans == (0,)


def test_import_categorizes_card_rows_from_chase(loaded: duckdb.DuckDBPyConnection) -> None:
    assert _category_of(loaded, "SYNTH GROCER #123") == [
        ("Groceries", "chase", CHASE_FALLBACK_CONF)
    ]
    assert _category_of(loaded, "LATE FEE") == [("Fees & Charges", "chase", CHASE_FALLBACK_CONF)]


def test_seed_rules_categorize_checking_rows(loaded: duckdb.DuckDBPyConnection) -> None:
    assert _category_of(loaded, "SYNTH EMPLOYER PAYROLL") == [("Paycheck", "rule", RULE_CONF)]
    assert _category_of(loaded, "MONTHLY SERVICE FEE") == [("Bank Fees", "rule", RULE_CONF)]
    assert _category_of(loaded, "ATM WITHDRAWAL 000000 01/20 SYNTH ST") == [
        ("Cash & ATM", "rule", RULE_CONF)
    ]
    # No rule, no merchant default, no Chase category: stays uncategorized.
    assert _category_of(loaded, "SYNTH RENT CO WEB PMTS") == [(None, None, None)]


def test_rerun_changes_nothing(loaded: duckdb.DuckDBPyConnection) -> None:
    assert run_categorization(loaded) == 0


def test_user_merchant_default_applies_to_all_its_rows(loaded: duckdb.DuckDBPyConnection) -> None:
    coffee = _category_id(loaded, "Coffee Shops")
    loaded.execute(
        "UPDATE merchants SET default_category_id = ?, source = 'user' WHERE id = ?",
        [coffee, _merchant_id(loaded, "SYNTH COFFEE CO")],
    )
    assert run_categorization(loaded) == 2
    assert _category_of(loaded, "SYNTH COFFEE CO") == [("Coffee Shops", "user", 1.0)] * 2
    # And it's recomputed, not frozen: changing the default moves them again.
    loaded.execute(
        "UPDATE merchants SET default_category_id = ? WHERE normalized_key = 'SYNTH COFFEE CO'",
        [_category_id(loaded, "Restaurants")],
    )
    run_categorization(loaded)
    assert _category_of(loaded, "SYNTH COFFEE CO") == [("Restaurants", "user", 1.0)] * 2


def test_override_is_locked(loaded: duckdb.DuckDBPyConnection) -> None:
    (txn_id,) = loaded.execute(
        "SELECT id FROM transactions WHERE raw_description = 'SYNTH GROCER #123'"
    ).fetchone() or ("",)
    assert repository.set_transaction_category(loaded, str(txn_id), _category_id(loaded, "Gifts"))
    # A rule that would otherwise match, and a merchant default, both lose to the override.
    loaded.execute(
        "INSERT INTO category_rules (pattern, category_id, priority, created_by) "
        "VALUES ('GROCER', ?, 1, 'user')",
        [_category_id(loaded, "Groceries")],
    )
    run_categorization(loaded)
    assert _category_of(loaded, "SYNTH GROCER #123") == [("Gifts", "user", 1.0)]


def test_user_rule_beats_seed_rule(loaded: duckdb.DuckDBPyConnection) -> None:
    loaded.execute(
        "INSERT INTO category_rules (pattern, category_id, created_by) "
        "VALUES ('ATM WITHDRAWAL', ?, 'user')",  # default priority 100 < seed 200
        [_category_id(loaded, "Gifts")],
    )
    run_categorization(loaded)
    assert _category_of(loaded, "ATM WITHDRAWAL 000000 01/20 SYNTH ST") == [
        ("Gifts", "rule", RULE_CONF)
    ]


def test_mapping_edit_reapplies(loaded: duckdb.DuckDBPyConnection) -> None:
    loaded.execute(
        "UPDATE chase_category_mappings SET category_id = ? WHERE chase_category = 'Food & Drink'",
        [_category_id(loaded, "Coffee Shops")],
    )
    assert run_categorization(loaded) == 2
    assert (
        _category_of(loaded, "SYNTH COFFEE CO")
        == [("Coffee Shops", "chase", CHASE_FALLBACK_CONF)] * 2
    )


def test_mapping_removal_clears(loaded: duckdb.DuckDBPyConnection) -> None:
    loaded.execute("DELETE FROM chase_category_mappings WHERE chase_category = 'Groceries'")
    assert run_categorization(loaded) == 1
    assert _category_of(loaded, "SYNTH GROCER #123") == [(None, None, None)]


def test_spend_by_parent_category_exact_cents(loaded: duckdb.DuckDBPyConnection) -> None:
    totals = dict(
        loaded.execute(
            "SELECT COALESCE(parent_category, 'Uncategorized'), sum(amount_cents) "
            "FROM v_spend GROUP BY 1"
        ).fetchall()
    )
    assert totals == {
        "Entertainment": -1549,
        "Fees & Charges": -1500 - 1200,  # card late fee + checking service fee (rule)
        "Shopping": -12999 + 2999,
        "Food & Dining": -8219 - 475 - 475,
        "Cash & ATM": -6000,
        "Income": 250000,  # payroll (rule)
        # Checking rows no rule covers: shop, check, market, rent.
        "Uncategorized": -1000 - 15000 - 2340 - 120000,
    }


def test_pipeline_leaves_no_dangling_references(loaded: duckdb.DuckDBPyConnection) -> None:
    assert repository.integrity_problems(loaded) == []
