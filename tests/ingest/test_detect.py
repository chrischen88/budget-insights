import pytest

from spendsight.ingest.detect import (
    CHASE_CARD_HEADER,
    CHASE_CHECKING_HEADER,
    decode,
    detect_format,
    read_table,
)
from spendsight.ingest.models import IngestError

CARD_HEADER = ",".join(CHASE_CARD_HEADER)
CHECKING_HEADER = ",".join(CHASE_CHECKING_HEADER)


def test_detects_card_header() -> None:
    assert detect_format(list(CHASE_CARD_HEADER)) == "chase_card"


def test_detects_checking_header() -> None:
    assert detect_format(list(CHASE_CHECKING_HEADER)) == "chase_checking"


def test_header_with_trailing_comma_and_spaces() -> None:
    assert detect_format([f" {h} " for h in CHASE_CHECKING_HEADER] + [""]) == "chase_checking"


def test_unknown_header_lists_expected_formats() -> None:
    with pytest.raises(IngestError) as exc:
        detect_format(["Date", "Payee", "Amount"])
    message = str(exc.value)
    assert "chase_card" in message
    assert "chase_checking" in message


@pytest.mark.parametrize("text", ["", "\n", " , ,\n"])
def test_empty_file(text: str) -> None:
    with pytest.raises(IngestError, match="empty"):
        read_table(text)


def test_header_only_has_no_rows() -> None:
    assert read_table(CARD_HEADER + "\n").rows == []


def test_blank_lines_skipped() -> None:
    text = f"{CARD_HEADER}\n\n01/03/2026,01/04/2026,X,Shopping,Sale,-1.00,\n\n"
    assert len(read_table(text).rows) == 1


def test_trailing_comma_rows_align() -> None:
    text = f"{CHECKING_HEADER}\nDEBIT,01/05/2026,X,-1.00,ACH_DEBIT,9.00,,\n"
    ((_, fields),) = read_table(text).rows
    assert fields == ["DEBIT", "01/05/2026", "X", "-1.00", "ACH_DEBIT", "9.00", ""]


def test_mixed_trailing_comma_and_plain_rows() -> None:
    text = (
        f"{CHECKING_HEADER}\n"
        "DEBIT,01/05/2026,X,-1.00,ACH_DEBIT,9.00,,\n"
        "DEBIT,01/06/2026,Y,-2.00,ACH_DEBIT,7.00,\n"
    )
    rows = read_table(text).rows
    assert [fields[3] for _, fields in rows] == ["-1.00", "-2.00"]
    assert all(len(fields) == 7 for _, fields in rows)


def test_omitted_final_column_tolerated() -> None:
    text = f"{CARD_HEADER}\n01/03/2026,01/04/2026,X,Shopping,Sale,-1.00\n"
    ((_, fields),) = read_table(text).rows
    assert fields[-1] == ""


def test_truncated_row_rejected_with_line_number() -> None:
    text = f"{CARD_HEADER}\n01/03/2026,01/04/2026,X,Shopping\n"
    with pytest.raises(IngestError, match="line 2"):
        read_table(text)


def test_extra_non_empty_column_rejected() -> None:
    text = f"{CHECKING_HEADER}\nDEBIT,01/05/2026,X,-1.00,ACH_DEBIT,9.00,,surprise\n"
    with pytest.raises(IngestError, match="expected 7 columns"):
        read_table(text)


def test_decode_strips_bom_and_falls_back_to_latin1() -> None:
    assert decode("﻿abc".encode()) == "abc"
    assert decode("CAF\xc9".encode("latin-1")) == "CAF\xc9"
