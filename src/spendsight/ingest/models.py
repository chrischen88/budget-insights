"""Types shared by the ingest parsers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from spendsight.db.repository import AccountKind

FileFormat = Literal["chase_card", "chase_checking"]

FORMAT_ACCOUNT_KIND: dict[FileFormat, AccountKind] = {
    "chase_card": "credit_card",
    "chase_checking": "checking",
}


class IngestError(ValueError):
    """A file could not be imported. Messages avoid echoing descriptions or amounts."""


@dataclass(frozen=True)
class ParsedTransaction:
    """One statement row, normalized: integer cents, negative = outflow."""

    txn_date: date
    post_date: date | None
    amount_cents: int
    raw_description: str
    chase_category: str | None = None
    chase_type: str | None = None
    memo: str | None = None


def parse_chase_date(raw: str, *, line: int, column: str) -> date:
    try:
        return datetime.strptime(raw.strip(), "%m/%d/%Y").date()  # noqa: DTZ007 - date only
    except ValueError as exc:
        raise IngestError(f"line {line}: {column} is not a MM/DD/YYYY date") from exc


def optional_text(raw: str) -> str | None:
    value = raw.strip()
    return value or None
