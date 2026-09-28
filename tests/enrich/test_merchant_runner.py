import duckdb

from spendsight.enrich.merchant_runner import run_merchant_linking
from spendsight.pipeline import import_and_process
from tests.conftest import fixture_bytes


def _rows(db: duckdb.DuckDBPyConnection, sql: str) -> list[tuple[object, ...]]:
    return db.execute(sql).fetchall()


def test_import_links_every_transaction(db: duckdb.DuckDBPyConnection) -> None:
    summary = import_and_process(
        db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000"
    )
    assert summary.merchants_linked == 8
    assert _rows(db, "SELECT count(*) FROM transactions WHERE merchant_id IS NULL") == [(0,)]


def test_variants_share_one_merchant(db: duckdb.DuckDBPyConnection) -> None:
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    import_and_process(db, fixture_bytes("chase_card_overlap.csv"), filename="o.csv", last4="0000")
    coffee = _rows(
        db,
        "SELECT count(DISTINCT merchant_id), count(*) FROM transactions "
        "WHERE raw_description = 'SYNTH COFFEE CO'",
    )
    assert coffee == [(1, 3)]
    # Sale and return at the same store are one merchant.
    store = _rows(
        db,
        "SELECT count(DISTINCT merchant_id) FROM transactions "
        "WHERE raw_description = 'SYNTH ONLINE STORE'",
    )
    assert store == [(1,)]


def test_new_merchants_have_only_a_key(db: duckdb.DuckDBPyConnection) -> None:
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    rows = _rows(
        db,
        "SELECT normalized_key, clean_name, source, default_category_id FROM merchants "
        "WHERE normalized_key = 'SYNTH GROCER'",
    )
    assert rows == [("SYNTH GROCER", None, None, None)]


def test_views_show_key_until_clean_name_exists(db: duckdb.DuckDBPyConnection) -> None:
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    shown = "SELECT DISTINCT merchant FROM v_spend WHERE merchant LIKE 'SYNTH GROCER%'"
    assert _rows(db, shown) == [("SYNTH GROCER",)]
    db.execute(
        "UPDATE merchants SET clean_name = 'Synth Grocer', source = 'user', confidence = 1.0 "
        "WHERE normalized_key = 'SYNTH GROCER'"
    )
    assert _rows(db, "SELECT DISTINCT merchant FROM v_spend WHERE merchant LIKE 'Synth%'") == [
        ("Synth Grocer",)
    ]


def test_rerun_links_nothing_new(db: duckdb.DuckDBPyConnection) -> None:
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    merchants_before = _rows(db, "SELECT count(*) FROM merchants")
    assert run_merchant_linking(db) == 0
    assert _rows(db, "SELECT count(*) FROM merchants") == merchants_before


def test_reimport_reuses_existing_merchants(db: duckdb.DuckDBPyConnection) -> None:
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    (before,) = _rows(db, "SELECT count(*) FROM merchants")
    summary = import_and_process(
        db, fixture_bytes("chase_card_overlap.csv"), filename="o.csv", last4="0000"
    )
    assert summary.merchants_linked == 2  # only the 2 new rows
    (after,) = _rows(db, "SELECT count(*) FROM merchants")
    assert after == (before[0] + 1,)  # coffee already known; gas station is new
