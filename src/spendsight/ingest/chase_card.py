"""Chase credit card CSV rows -> ParsedTransaction (SPEC.md §3.1)."""

from __future__ import annotations

from spendsight.ingest.models import (
    IngestError,
    ParsedTransaction,
    optional_text,
    parse_chase_date,
)
from spendsight.money import MoneyParseError, parse_cents


def parse_rows(rows: list[tuple[int, list[str]]]) -> list[ParsedTransaction]:
    """Card amounts already follow our sign convention: purchases negative, payments positive."""
    parsed = []
    for line, (txn_date, post_date, description, category, kind, amount, memo) in rows:
        try:
            cents = parse_cents(amount)
        except MoneyParseError as exc:
            raise IngestError(f"line {line}: Amount is not a valid amount") from exc
        desc = description.strip()
        if not desc:
            raise IngestError(f"line {line}: Description is empty")
        parsed.append(
            ParsedTransaction(
                txn_date=parse_chase_date(txn_date, line=line, column="Transaction Date"),
                post_date=(
                    parse_chase_date(post_date, line=line, column="Post Date")
                    if post_date.strip()
                    else None
                ),
                amount_cents=cents,
                raw_description=desc,
                chase_category=optional_text(category),
                chase_type=optional_text(kind),
                memo=optional_text(memo),
            )
        )
    return parsed
