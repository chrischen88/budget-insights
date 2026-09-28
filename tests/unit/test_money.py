import pytest

from spendsight.money import MoneyParseError, format_cents, parse_cents


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0),
        ("0.00", 0),
        ("12.34", 1234),
        ("-12.34", -1234),
        ("-0.01", -1),
        ("1,234.56", 123456),
        ("$99", 9900),
        ("-$5.5", -550),
        ("(4.50)", -450),
        ("  7.10 ", 710),
        # Classic float trap: 0.1 + 0.2 style values must stay exact.
        ("1234567.89", 123456789),
        ("0.29", 29),
    ],
)
def test_parse_cents(raw: str, expected: int) -> None:
    assert parse_cents(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "abc", "1.234", "NaN", "Infinity", "1.2.3"])
def test_parse_cents_rejects(raw: str) -> None:
    with pytest.raises(MoneyParseError):
        parse_cents(raw)


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (0, "$0.00"),
        (5, "$0.05"),
        (123456, "$1,234.56"),
        (-123456, "-$1,234.56"),
        (-1, "-$0.01"),
        (100000000, "$1,000,000.00"),
    ],
)
def test_format_cents(cents: int, expected: str) -> None:
    assert format_cents(cents) == expected


def test_format_cents_unsigned() -> None:
    assert format_cents(-4250, signed=False) == "$42.50"
