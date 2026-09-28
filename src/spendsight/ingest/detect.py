"""Read CSV text into rows and detect the Chase format from the header (never the filename)."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from spendsight.ingest.models import FileFormat, IngestError

CHASE_CARD_HEADER = (
    "Transaction Date",
    "Post Date",
    "Description",
    "Category",
    "Type",
    "Amount",
    "Memo",
)
CHASE_CHECKING_HEADER = (
    "Details",
    "Posting Date",
    "Description",
    "Amount",
    "Type",
    "Balance",
    "Check or Slip #",
)
HEADERS: dict[FileFormat, tuple[str, ...]] = {
    "chase_card": CHASE_CARD_HEADER,
    "chase_checking": CHASE_CHECKING_HEADER,
}


@dataclass(frozen=True)
class CsvTable:
    """Data rows, each exactly as wide as the header, with the file line they came from."""

    format: FileFormat
    rows: list[tuple[int, list[str]]]


def _strip_trailing_empty(fields: list[str], width: int) -> list[str]:
    """Drop empty cells past `width` (Chase checking rows end with a trailing comma)."""
    while len(fields) > width and fields[-1].strip() == "":
        fields = fields[:-1]
    return fields


def decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Older exports are Windows-1252; latin-1 decodes any byte sequence.
        return content.decode("latin-1")


def detect_format(header: list[str]) -> FileFormat:
    cells = tuple(h.strip() for h in header)
    while cells and cells[-1] == "":
        cells = cells[:-1]
    for fmt, expected in HEADERS.items():
        if cells == expected:
            return fmt
    expected_list = "; ".join(f"{fmt}: {','.join(cols)}" for fmt, cols in HEADERS.items())
    raise IngestError(f"unrecognized CSV header. Expected one of -> {expected_list}")


def read_table(text: str) -> CsvTable:
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None or not any(cell.strip() for cell in header):
        raise IngestError("file is empty")
    fmt = detect_format(header)
    width = len(HEADERS[fmt])
    rows = []
    for fields in reader:
        line = reader.line_num
        if not any(cell.strip() for cell in fields):
            continue  # blank line
        fields = _strip_trailing_empty(fields, width)
        if len(fields) == width - 1:
            # Tolerate an omitted final column (Memo / Check #); anything shorter is truncated.
            fields = [*fields, ""]
        if len(fields) != width:
            raise IngestError(f"line {line}: expected {width} columns, found {len(fields)}")
        rows.append((line, fields))
    return CsvTable(fmt, rows)
