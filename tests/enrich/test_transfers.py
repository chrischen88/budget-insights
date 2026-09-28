from datetime import date, timedelta

import pytest

from spendsight.enrich.transfers import TransferPair, TransferRow, detect_transfers, is_candidate

D0 = date(2026, 1, 15)


def row(
    id: str,
    amount: int,
    *,
    account: str = "chase-checking-0000",
    kind: str = "checking",
    last4: str | None = "0000",
    desc: str = "Payment to Chase card ending in 1111 01/15",
    chase_type: str | None = "ACCT_XFER",
    days: int = 0,
) -> TransferRow:
    return TransferRow(
        id, account, kind, last4, D0 + timedelta(days=days), amount, desc, chase_type
    )


def card(id: str, amount: int, *, last4: str = "1111", days: int = 0) -> TransferRow:
    return row(
        id,
        amount,
        account=f"chase-card-{last4}",
        kind="credit_card",
        last4=last4,
        desc="Payment Thank You-Mobile",
        chase_type="Payment",
        days=days,
    )


@pytest.mark.parametrize(
    ("desc", "expected"),
    [
        ("CHASE CREDIT CRD AUTOPAY PPD ID: 0000000000", True),
        ("Payment to Chase card ending in 1111 01/15", True),
        ("Online Transfer to CHK ...2222 transaction#: 000000 01/15", True),
        ("ONLINE TRANSFER FROM SAV ...3333", True),
        ("SYNTH RENT CO WEB PMTS", False),
        ("Zelle payment to SYNTH PERSON", False),
        ("SYNTH TRANSFER STATION RECYCLING", False),
    ],
)
def test_checking_candidate_patterns(desc: str, expected: bool) -> None:
    assert is_candidate(row("x", -100, desc=desc)) is expected


@pytest.mark.parametrize(
    ("chase_type", "expected"),
    [("Payment", True), ("payment", True), ("Sale", False), ("Return", False), (None, False)],
)
def test_card_candidate_by_type(chase_type: str | None, expected: bool) -> None:
    r = row("x", 100, kind="credit_card", desc="ANYTHING", chase_type=chase_type)
    assert is_candidate(r) is expected


def test_pairs_card_payment_with_checking_outflow() -> None:
    result = detect_transfers([row("out", -50000), card("in", 50000, days=1)])
    assert result.pairs == [TransferPair("out", "in")]
    assert result.unpaired_ids == set()


@pytest.mark.parametrize(("days", "paired"), [(5, True), (-5, True), (6, False), (-6, False)])
def test_window_is_plus_minus_five_days(days: int, paired: bool) -> None:
    result = detect_transfers([row("out", -50000), card("in", 50000, days=days)])
    assert bool(result.pairs) is paired


def test_amounts_must_be_equal_and_opposite() -> None:
    result = detect_transfers([row("out", -50000), card("in", 49999)])
    assert result.pairs == []
    assert result.unpaired_ids == {"out", "in"}


def test_same_account_never_pairs() -> None:
    a = row("a", -1000, desc="Online Transfer to CHK ...2222")
    b = row("b", 1000, desc="Online Transfer from CHK ...2222")
    assert detect_transfers([a, b]).pairs == []


def test_card_last4_in_description_must_match() -> None:
    out = row("out", -50000, desc="Payment to Chase card ending in 9999 01/15")
    assert detect_transfers([out, card("in", 50000, last4="1111")]).pairs == []


def test_last4_picks_the_right_card() -> None:
    out = row("out", -50000, desc="Payment to Chase card ending in 2222 01/15")
    result = detect_transfers(
        [out, card("c1", 50000, last4="1111"), card("c2", 50000, last4="2222")]
    )
    assert result.pairs == [TransferPair("out", "c2")]
    assert result.unpaired_ids == {"c1"}


def test_one_to_one_closest_date_wins() -> None:
    result = detect_transfers(
        [row("out", -50000), card("far", 50000, days=4), card("near", 50000, days=1)]
    )
    assert result.pairs == [TransferPair("out", "near")]
    assert result.unpaired_ids == {"far"}


def test_non_candidates_are_ignored() -> None:
    rent = row("rent", -50000, desc="SYNTH RENT CO WEB PMTS", chase_type="ACH_DEBIT")
    refund = card("refund", 50000)
    refund = TransferRow(**{**refund.__dict__, "chase_type": "Return"})
    result = detect_transfers([rent, refund])
    assert result.pairs == []
    assert result.candidate_ids == set()


def test_own_checking_to_checking_transfer() -> None:
    out = row("out", -2500, desc="Online Transfer to CHK ...2222")
    inc = row(
        "in",
        2500,
        account="chase-checking-2222",
        last4="2222",
        desc="Online Transfer from CHK ...0000",
    )
    assert detect_transfers([out, inc]).pairs == [TransferPair("out", "in")]


def test_deterministic_regardless_of_input_order() -> None:
    rows = [row("out", -50000), card("a", 50000, days=2), card("b", 50000, days=-2)]
    assert detect_transfers(rows).pairs == detect_transfers(list(reversed(rows))).pairs
