"""Import a Chase CSV: detect -> parse -> assign IDs -> store, in one DB transaction."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass

import duckdb

from spendsight.db import repository
from spendsight.db.repository import AccountKind
from spendsight.ingest import chase_card, chase_checking
from spendsight.ingest.dedupe import assign_ids, to_frame
from spendsight.ingest.detect import decode, read_table
from spendsight.ingest.models import (
    FORMAT_ACCOUNT_KIND,
    FileFormat,
    IngestError,
    ParsedTransaction,
)

_LAST4 = re.compile(r"[0-9]{4}")
# Chase names exports like "Chase1234_Activity20260101_20260131_20260201.CSV".
_FILENAME_LAST4 = re.compile(r"chase(\d{4})(?!\d)", re.IGNORECASE)
_KIND_SLUG: dict[AccountKind, str] = {"credit_card": "card", "checking": "checking"}
_KIND_LABEL: dict[AccountKind, str] = {"credit_card": "Chase Card", "checking": "Chase Checking"}


@dataclass(frozen=True)
class ParsedFile:
    format: FileFormat
    transactions: list[ParsedTransaction]

    @property
    def account_kind(self) -> AccountKind:
        return FORMAT_ACCOUNT_KIND[self.format]


@dataclass(frozen=True)
class ImportResult:
    account_id: str
    format: FileFormat
    rows_in_file: int
    new_rows: int
    # True when this exact file (same bytes) was imported before; nothing was written.
    already_imported: bool

    @property
    def duplicate_rows(self) -> int:
        return self.rows_in_file - self.new_rows


def parse_file(content: bytes) -> ParsedFile:
    """Pure: bytes of a Chase CSV -> normalized transactions. Used for preview and import."""
    table = read_table(decode(content))
    if table.format == "chase_card":
        txns = chase_card.parse_rows(table.rows)
    else:
        txns = chase_checking.parse_rows(table.rows)
    return ParsedFile(table.format, txns)


def guess_last4(filename: str) -> str | None:
    """Suggest the account's last4 from a Chase export filename.

    Only a pre-filled suggestion for the user to confirm: the file's format always comes
    from its header, and the user can override the account.
    """
    match = _FILENAME_LAST4.search(filename)
    return match.group(1) if match else None


def account_id_for(kind: AccountKind, last4: str) -> str:
    return f"chase-{_KIND_SLUG[kind]}-{last4}"


def import_file(
    conn: duckdb.DuckDBPyConnection,
    content: bytes,
    *,
    filename: str,
    last4: str,
    display_name: str | None = None,
) -> ImportResult:
    """Import one file into the account identified by its kind (from the header) and last4.

    Idempotent: an identical file is skipped entirely, and overlapping rows from a
    different file collide on their deterministic IDs and are not inserted again.
    """
    if not _LAST4.fullmatch(last4):
        raise IngestError("account last4 must be exactly 4 digits")
    parsed = parse_file(content)
    kind = parsed.account_kind
    account = repository.Account(
        id=account_id_for(kind, last4),
        kind=kind,
        display_name=display_name or f"{_KIND_LABEL[kind]} ••{last4}",
        last4=last4,
    )
    rows = assign_ids(account.id, parsed.transactions)
    file_sha256 = hashlib.sha256(content).hexdigest()

    conn.execute("BEGIN TRANSACTION")
    try:
        if repository.find_import_by_sha(conn, file_sha256) is not None:
            conn.execute("ROLLBACK")
            return ImportResult(account.id, parsed.format, len(rows), 0, already_imported=True)
        repository.ensure_account(conn, account)
        import_id = str(uuid.uuid4())
        repository.insert_import(
            conn,
            import_id=import_id,
            account_id=account.id,
            filename=filename,
            file_sha256=file_sha256,
            row_count=len(rows),
        )
        new_rows = repository.insert_transactions(
            conn, import_id=import_id, account_id=account.id, staged=to_frame(rows)
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return ImportResult(account.id, parsed.format, len(rows), new_rows, already_imported=False)
