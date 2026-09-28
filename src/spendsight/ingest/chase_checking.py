"""Chase checking CSV rows -> ParsedTransaction (SPEC.md §3.2).

Checking exports have only a posting date, which is used as both txn_date and post_date.
`Details` and `Balance` are not stored (the schema has no column for them); the check
number, when present, goes in `memo`.
"""

from __future__ import annotations

from spendsight.ingest.models import (
    IngestError,
    ParsedTransaction,
    optional_text,
    parse_chase_date,
)
from spendsight.money import MoneyParseError, parse_cents


def parse_rows(rows: list[tuple[int, list[str]]]) -> list[ParsedTransaction]:
    parsed = []
    for line, (_details, posting_date, description, amount, kind, _balance, check_no) in rows:
        try:
            cents = parse_cents(amount)
        except MoneyParseError as exc:
            raise IngestError(f"line {line}: Amount is not a valid amount") from exc
        desc = description.strip()
        if not desc:
            raise IngestError(f"line {line}: Description is empty")
        posted = parse_chase_date(posting_date, line=line, column="Posting Date")
        check = check_no.strip()
        parsed.append(
            ParsedTransaction(
                txn_date=posted,
                post_date=posted,
                amount_cents=cents,
                raw_description=desc,
                chase_type=optional_text(kind),
                memo=f"Check #{check}" if check else None,
            )
        )
    return parsed
