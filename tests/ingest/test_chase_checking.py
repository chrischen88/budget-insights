from datetime import date

from spendsight.ingest.importer import parse_file
from tests.conftest import fixture_bytes


def test_fixture_parses_as_checking_with_trailing_commas() -> None:
    parsed = parse_file(fixture_bytes("chase_checking_jan.csv"))
    assert parsed.format == "chase_checking"
    assert parsed.account_kind == "checking"
    assert len(parsed.transactions) == 8


def test_fixture_total_exact_cents() -> None:
    # -10.00 - 60.00 - 12.00 - 500.00 - 150.00 - 23.40 - 1200.00 + 2500.00 = 544.60
    parsed = parse_file(fixture_bytes("chase_checking_jan.csv"))
    assert sum(t.amount_cents for t in parsed.transactions) == 54460


def test_columns_not_misaligned() -> None:
    txns = parse_file(fixture_bytes("chase_checking_jan.csv")).transactions
    payroll = txns[-1]
    assert payroll.txn_date == date(2026, 1, 2)
    assert payroll.post_date == date(2026, 1, 2)
    assert payroll.amount_cents == 250000
    assert payroll.chase_type == "ACH_CREDIT"
    assert payroll.chase_category is None
    assert payroll.memo is None


def test_quoted_comma_in_description() -> None:
    txns = parse_file(fixture_bytes("chase_checking_jan.csv")).transactions
    assert txns[0].raw_description == "SYNTH SHOP, INC"
    assert txns[0].amount_cents == -1000


def test_check_number_in_memo() -> None:
    txns = parse_file(fixture_bytes("chase_checking_jan.csv")).transactions
    (check,) = [t for t in txns if t.chase_type == "CHECK_PAID"]
    assert check.memo == "Check #1001"
    assert check.amount_cents == -15000


def test_same_rows_without_trailing_commas_parse_identically() -> None:
    with_commas = fixture_bytes("chase_checking_jan.csv")
    lines = with_commas.decode().splitlines()
    stripped = "\n".join([lines[0], *(line.removesuffix(",") for line in lines[1:])])
    assert parse_file(stripped.encode()).transactions == parse_file(with_commas).transactions
