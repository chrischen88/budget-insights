"""Money helpers. All amounts are integer cents; negative = outflow, positive = inflow."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

_CENT = Decimal("0.01")


class MoneyParseError(ValueError):
    """Raised when a string cannot be parsed as an exact cents amount."""


def parse_cents(raw: str) -> int:
    """Parse a decimal amount string (e.g. "-1,234.56", "$12", "(4.50)") to integer cents.

    Parsing goes through Decimal, never float. Amounts with fractional cents are
    rejected rather than rounded, since a statement never contains them.
    """
    text = raw.strip().replace(",", "").replace("$", "")
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    if not text:
        raise MoneyParseError("empty amount")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise MoneyParseError(f"not a number: {raw!r}") from exc
    if not value.is_finite():
        raise MoneyParseError(f"not a finite amount: {raw!r}")
    if value != value.quantize(_CENT):
        raise MoneyParseError(f"fractional cents: {raw!r}")
    cents = int(value * 100)
    return -cents if negative else cents


def format_cents(cents: int, *, signed: bool = True) -> str:
    """Format cents for display: -123456 -> "-$1,234.56".

    With `signed=False` the sign is dropped (useful for charts of spend magnitude).
    """
    sign = "-" if cents < 0 and signed else ""
    dollars, rem = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{rem:02d}"
