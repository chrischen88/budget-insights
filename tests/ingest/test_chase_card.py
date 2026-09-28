from datetime import date

import pytest

from spendsight.ingest.importer import parse_file
from spendsight.ingest.models import IngestError
from tests.conftest import fixture_bytes

HEADER = b"Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"


def test_fixture_parses_as_card() -> None:
    parsed = parse_file(fixture_bytes("chase_card_jan.csv"))
    assert parsed.format == "chase_card"
    assert parsed.account_kind == "credit_card"
    assert len(parsed.transactions) == 8


def test_fixture_total_exact_cents() -> None:
    # -15.49 + 500.00 - 15.00 + 29.99 - 129.99 - 82.19 - 4.75 - 4.75 = 277.82
    parsed = parse_file(fixture_bytes("chase_card_jan.csv"))
    assert sum(t.amount_cents for t in parsed.transactions) == 27782


def test_row_fields() -> None:
    txns = parse_file(fixture_bytes("chase_card_jan.csv")).transactions
    streaming = txns[0]
    assert streaming.txn_date == date(2026, 1, 20)
    assert streaming.post_date == date(2026, 1, 21)
    assert streaming.amount_cents == -1549
    assert streaming.raw_description == "SYNTH STREAMING"
    assert streaming.chase_category == "Entertainment"
    assert streaming.chase_type == "Sale"
    assert streaming.memo == "Synthetic memo"


def test_payment_refund_and_fee_signs() -> None:
    txns = parse_file(fixture_bytes("chase_card_jan.csv")).transactions
    by_type = {t.chase_type: t for t in txns}
    assert by_type["Payment"].amount_cents == 50000
    assert by_type["Payment"].chase_category is None
    assert by_type["Return"].amount_cents == 2999
    assert by_type["Fee"].amount_cents == -1500


def test_duplicate_same_day_rows_both_kept() -> None:
    txns = parse_file(fixture_bytes("chase_card_jan.csv")).transactions
    assert sum(1 for t in txns if t.raw_description == "SYNTH COFFEE CO") == 2


def test_bad_amount_error_does_not_echo_value() -> None:
    content = HEADER + b"01/03/2026,01/04/2026,X,Shopping,Sale,12.3.4,\n"
    with pytest.raises(IngestError) as exc:
        parse_file(content)
    assert "line 2" in str(exc.value)
    assert "12.3.4" not in str(exc.value)


def test_bad_date() -> None:
    with pytest.raises(IngestError, match="Transaction Date"):
        parse_file(HEADER + b"2026-01-03,01/04/2026,X,Shopping,Sale,-1.00,\n")


def test_blank_post_date_allowed() -> None:
    (txn,) = parse_file(HEADER + b"01/03/2026,,X,Shopping,Sale,-1.00,\n").transactions
    assert txn.post_date is None


def test_empty_description_rejected() -> None:
    with pytest.raises(IngestError, match="Description"):
        parse_file(HEADER + b"01/03/2026,01/04/2026, ,Shopping,Sale,-1.00,\n")
