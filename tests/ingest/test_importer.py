import duckdb
import pytest

from spendsight.db import repository
from spendsight.ingest.importer import guess_last4, import_file
from spendsight.ingest.models import IngestError
from tests.conftest import fixture_bytes


def _count(db: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = db.execute(sql).fetchone()
    assert row is not None
    return int(row[0])


def _total(db: duckdb.DuckDBPyConnection) -> int:
    return _count(db, "SELECT COALESCE(sum(amount_cents), 0) FROM transactions")


def test_import_card(db: duckdb.DuckDBPyConnection) -> None:
    result = import_file(db, fixture_bytes("chase_card_jan.csv"), filename="a.csv", last4="0000")
    assert result.account_id == "chase-card-0000"
    assert (result.rows_in_file, result.new_rows, result.duplicate_rows) == (8, 8, 0)
    assert not result.already_imported
    assert _total(db) == 27782
    account = repository.get_account(db, "chase-card-0000")
    assert account is not None
    assert account.kind == "credit_card"
    assert account.last4 == "0000"


def test_reimport_identical_file_is_noop(db: duckdb.DuckDBPyConnection) -> None:
    content = fixture_bytes("chase_card_jan.csv")
    import_file(db, content, filename="a.csv", last4="0000")
    again = import_file(db, content, filename="renamed.csv", last4="0000")
    assert again.already_imported
    assert again.new_rows == 0
    assert _count(db, "SELECT count(*) FROM transactions") == 8
    assert _count(db, "SELECT count(*) FROM imports") == 1


def test_overlapping_import_adds_only_new_rows(db: duckdb.DuckDBPyConnection) -> None:
    import_file(db, fixture_bytes("chase_card_jan.csv"), filename="jan.csv", last4="0000")
    overlap = import_file(
        db, fixture_bytes("chase_card_overlap.csv"), filename="late-jan.csv", last4="0000"
    )
    assert (overlap.rows_in_file, overlap.new_rows, overlap.duplicate_rows) == (4, 2, 2)
    assert _count(db, "SELECT count(*) FROM transactions") == 10
    # 277.82 + (-4.75 coffee on 01/28) + (-40.00 gas) = 233.07
    assert _total(db) == 23307


def test_same_day_duplicates_survive_reimport(db: duckdb.DuckDBPyConnection) -> None:
    import_file(db, fixture_bytes("chase_card_jan.csv"), filename="jan.csv", last4="0000")
    coffee = "SELECT count(*) FROM transactions WHERE raw_description = 'SYNTH COFFEE CO'"
    assert _count(db, coffee) == 2
    import_file(db, fixture_bytes("chase_card_overlap.csv"), filename="late.csv", last4="0000")
    assert _count(db, coffee) == 3  # the two 01/03 charges + one on 01/28


def test_import_checking(db: duckdb.DuckDBPyConnection) -> None:
    result = import_file(
        db, fixture_bytes("chase_checking_jan.csv"), filename="c.csv", last4="0000"
    )
    assert result.account_id == "chase-checking-0000"
    assert result.new_rows == 8
    assert _total(db) == 54460


def test_card_and_checking_with_same_last4_are_separate(db: duckdb.DuckDBPyConnection) -> None:
    import_file(db, fixture_bytes("chase_card_jan.csv"), filename="a.csv", last4="0000")
    import_file(db, fixture_bytes("chase_checking_jan.csv"), filename="b.csv", last4="0000")
    assert {a.id for a in repository.list_accounts(db)} == {
        "chase-card-0000",
        "chase-checking-0000",
    }
    assert _total(db) == 27782 + 54460


@pytest.mark.parametrize("last4", ["123", "12345", "abcd", ""])
def test_invalid_last4_rejected(db: duckdb.DuckDBPyConnection, last4: str) -> None:
    with pytest.raises(IngestError, match="last4"):
        import_file(db, fixture_bytes("chase_card_jan.csv"), filename="a.csv", last4=last4)


def test_bad_file_writes_nothing(db: duckdb.DuckDBPyConnection) -> None:
    bad = b"Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n" + (
        b"01/03/2026,01/04/2026,X,Shopping,Sale,oops,\n"
    )
    with pytest.raises(IngestError):
        import_file(db, bad, filename="bad.csv", last4="0000")
    assert _count(db, "SELECT count(*) FROM imports") == 0
    assert _count(db, "SELECT count(*) FROM accounts") == 0


def test_unknown_format_rejected(db: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(IngestError, match="unrecognized"):
        import_file(db, b"Date,Payee,Amount\n01/01/2026,X,-1\n", filename="x.csv", last4="0000")


def test_header_only_file_imports_zero_rows(db: duckdb.DuckDBPyConnection) -> None:
    content = b"Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
    result = import_file(db, content, filename="empty.csv", last4="0000")
    assert (result.rows_in_file, result.new_rows) == (0, 0)


def test_new_rows_have_no_category_yet(db: duckdb.DuckDBPyConnection) -> None:
    import_file(db, fixture_bytes("chase_card_jan.csv"), filename="a.csv", last4="0000")
    assert _count(db, "SELECT count(*) FROM transactions WHERE category_id IS NOT NULL") == 0


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Chase1234_Activity20260101_20260131_20260201.CSV", "1234"),
        ("chase0000_Activity_20260201.csv", "0000"),
        ("Chase12345_Activity.csv", None),  # 5 digits: not a last4
        ("statement.csv", None),
        ("my-export-1234.csv", None),
    ],
)
def test_guess_last4(filename: str, expected: str | None) -> None:
    assert guess_last4(filename) == expected
