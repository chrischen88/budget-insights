import pytest

from spendsight.app.formatting import (
    category_label,
    date_range_label,
    preview_frame,
    recategorize_message,
    source_badge,
    transfer_candidates_frame,
)
from spendsight.ingest.importer import ParsedFile, parse_file
from tests.conftest import fixture_bytes


def test_preview_limits_rows_and_formats_amounts() -> None:
    frame = preview_frame(parse_file(fixture_bytes("chase_card_jan.csv")), limit=3)
    assert len(frame) == 3
    assert list(frame["Amount"]) == ["-$15.49", "$500.00", "-$15.00"]


def test_date_range_label() -> None:
    parsed = parse_file(fixture_bytes("chase_card_jan.csv"))
    assert date_range_label(parsed) == "Jan 03, 2026 to Jan 20, 2026"
    assert date_range_label(ParsedFile("chase_card", [])) == "no transactions"


def test_transfer_candidates_frame() -> None:
    from datetime import date

    frame = transfer_candidates_frame(
        [
            {
                "id": "x",
                "txn_date": date(2026, 1, 15),
                "account": "Chase Card ••0000",
                "raw_description": "Payment Thank You-Mobile",
                "amount_cents": 50000,
            }
        ]
    )
    assert frame.to_dict("records") == [
        {
            "Date": date(2026, 1, 15),
            "Account": "Chase Card ••0000",
            "Description": "Payment Thank You-Mobile",
            "Amount": "$500.00",
        }
    ]


@pytest.mark.parametrize(
    ("source", "conf", "locked", "expected"),
    [
        ("user", 1.0, True, "Edited by you"),
        ("user", 1.0, False, "Your merchant default"),
        ("rule", 1.0, False, "Rule"),
        ("llm", 0.82, False, "AI suggestion 82%"),
        ("ml", 0.9, False, "Learned 90%"),
        ("chase", 0.5, False, "Chase category 50%"),
        (None, None, False, ""),
    ],
)
def test_source_badge(source: str | None, conf: float | None, locked: bool, expected: str) -> None:
    assert source_badge(source, conf, locked) == expected


def test_category_label() -> None:
    assert category_label("Coffee Shops", "Food & Dining") == "Food & Dining / Coffee Shops"
    assert category_label("Travel", None) == "Travel"
    assert category_label(None, None) == "Uncategorized"


def test_recategorize_messages() -> None:
    common = {"category": "Coffee Shops", "merchant": "SYNTH COFFEE CO"}
    assert (
        recategorize_message(
            **common,
            applied_to_merchant=False,
            merchant_rows=0,
            following=0,
            held_by_override=0,
            held_by_rule=0,
        )
        == "Moved this transaction to Coffee Shops."
    )
    message = recategorize_message(
        **common,
        applied_to_merchant=True,
        merchant_rows=5,
        following=2,
        held_by_override=1,
        held_by_rule=2,
    )
    assert message.startswith("2 of 5 SYNTH COFFEE CO transactions are now Coffee Shops.")
    assert "2 stay where a rule puts them" in message
    assert "1 keep a category you set" in message


def test_recategorize_message_reports_relearned_rows() -> None:
    common = {
        "category": "Coffee Shops",
        "merchant": "SYNTH COFFEE CO",
        "merchant_rows": 3,
        "following": 3,
        "held_by_override": 0,
        "held_by_rule": 0,
    }
    assert recategorize_message(**common, applied_to_merchant=False, relearned=1) == (
        "Moved this transaction to Coffee Shops. Spendsight learned from this edit and "
        "recategorized 1 other transaction."
    )
    assert recategorize_message(**common, applied_to_merchant=True, relearned=4).endswith(
        "are now Coffee Shops. Spendsight learned from this edit and recategorized 4 other "
        "transactions."
    )
