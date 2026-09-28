import duckdb

from spendsight.db import repository
from spendsight.enrich.transfer_runner import run_transfer_detection
from spendsight.pipeline import import_and_process
from tests.conftest import fixture_bytes


def _one(db: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = db.execute(sql).fetchone()
    assert row is not None
    return int(row[0])


def _import_card(db: duckdb.DuckDBPyConnection) -> None:
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="card.csv", last4="0000")


def _import_checking(db: duckdb.DuckDBPyConnection) -> None:
    import_and_process(
        db, fixture_bytes("chase_checking_jan.csv"), filename="chk.csv", last4="0000"
    )


def test_payment_paired_on_both_sides(db: duckdb.DuckDBPyConnection) -> None:
    _import_card(db)
    summary = import_and_process(
        db, fixture_bytes("chase_checking_jan.csv"), filename="chk.csv", last4="0000"
    )
    assert summary.transfers.new_pairs == 1
    assert summary.transfers.unpaired_candidates == 0
    pair = db.execute(
        "SELECT a.account_id, a.amount_cents, b.account_id, b.amount_cents "
        "FROM transactions a JOIN transactions b ON b.id = a.transfer_pair_id "
        "WHERE a.is_transfer AND a.amount_cents < 0"
    ).fetchall()
    assert pair == [("chase-checking-0000", -50000, "chase-card-0000", 50000)]


def test_transfers_excluded_from_spend(db: duckdb.DuckDBPyConnection) -> None:
    _import_card(db)
    _import_checking(db)
    # Card 277.82 - 500.00 payment = -222.18; checking 544.60 + 500.00 = 1044.60.
    assert _one(db, "SELECT sum(amount_cents) FROM v_spend") == -22218 + 104460
    assert _one(db, "SELECT count(*) FROM v_spend") == 14


def test_unpaired_candidate_flagged_but_still_counted(db: duckdb.DuckDBPyConnection) -> None:
    _import_card(db)
    assert _one(db, "SELECT count(*) FROM transactions WHERE transfer_candidate") == 1
    assert _one(db, "SELECT count(*) FROM transactions WHERE is_transfer") == 0
    assert _one(db, "SELECT sum(amount_cents) FROM v_spend") == 27782


def test_pairs_when_other_side_arrives_later(db: duckdb.DuckDBPyConnection) -> None:
    _import_card(db)
    _import_checking(db)
    assert _one(db, "SELECT count(*) FROM transactions WHERE is_transfer") == 2
    assert (
        _one(db, "SELECT count(*) FROM transactions WHERE transfer_candidate AND NOT is_transfer")
        == 0
    )


def test_rerun_is_idempotent(db: duckdb.DuckDBPyConnection) -> None:
    _import_card(db)
    _import_checking(db)
    before = db.execute(
        "SELECT id, transfer_pair_id FROM transactions WHERE is_transfer ORDER BY id"
    ).fetchall()
    again = run_transfer_detection(db)
    assert again.new_pairs == 0
    after = db.execute(
        "SELECT id, transfer_pair_id FROM transactions WHERE is_transfer ORDER BY id"
    ).fetchall()
    assert before == after


def test_card_for_other_last4_not_paired(db: duckdb.DuckDBPyConnection) -> None:
    # Checking says "ending in 0000"; a card account 1111 must not claim that payment.
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="1111")
    summary = import_and_process(
        db, fixture_bytes("chase_checking_jan.csv"), filename="k.csv", last4="0000"
    )
    assert summary.transfers.new_pairs == 0
    assert summary.transfers.unpaired_candidates == 2


def test_review_list_and_mark_as_transfer(db: duckdb.DuckDBPyConnection) -> None:
    _import_card(db)
    (candidate,) = repository.unpaired_transfer_candidates(db)
    assert candidate["amount_cents"] == 50000
    assert candidate["account"] == "Chase Card ••0000"
    assert repository.mark_as_transfer(db, [str(candidate["id"])]) == 1
    assert repository.unpaired_transfer_candidates(db) == []
    # Confirmed payment no longer counts as income.
    assert _one(db, "SELECT sum(amount_cents) FROM v_spend") == 27782 - 50000
    # A later detection run leaves the confirmed row alone.
    assert run_transfer_detection(db).new_pairs == 0


def test_mark_as_transfer_only_affects_candidates(db: duckdb.DuckDBPyConnection) -> None:
    _import_card(db)
    (grocer_id,) = db.execute(
        "SELECT id FROM transactions WHERE raw_description = 'SYNTH GROCER #123'"
    ).fetchone() or ("",)
    assert repository.mark_as_transfer(db, [str(grocer_id)]) == 0
    assert repository.mark_as_transfer(db, []) == 0
