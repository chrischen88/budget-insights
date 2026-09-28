"""End-to-end test of the Import page against a temporary database (no network)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from spendsight.db.connection import connect
from tests.conftest import fixture_bytes

PAGE = str(Path(__file__).parents[2] / "src/spendsight/app/views/import_page.py")


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "app.duckdb"
    monkeypatch.setenv("SPENDSIGHT_DB_PATH", str(path))
    monkeypatch.setenv("SPENDSIGHT_LOCAL_ONLY", "true")
    st.cache_resource.clear()
    yield path
    st.cache_resource.clear()


def _metrics(at: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


def _count(path: Path, sql: str) -> int:
    conn = connect(path)
    try:
        row = conn.execute(sql).fetchone()
        assert row is not None
        return int(row[0])
    finally:
        conn.close()


def test_empty_state(db_path: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    assert [s.value for s in at.success] == ["No unpaired transfers to review."]


def test_import_card_file_with_suggested_last4(db_path: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    at.file_uploader[0].upload(
        "Chase0000_Activity20260101_20260131.CSV", fixture_bytes("chase_card_jan.csv")
    ).run()
    assert not at.exception
    assert at.text_input[0].value == "0000"
    at.button(key="import").click().run()
    assert not at.exception
    assert _metrics(at) == {
        "New transactions": "8",
        "Duplicates skipped": "0",
        "Transfers found": "0",
        "Categorized": "7",
    }
    # Local-only in tests: nothing is sent, and the page says so.
    assert any("AI merchant naming is off" in c.value for c in at.caption)
    # The card payment has no checking side yet, so it's listed for review.
    assert len(at.warning) == 1
    assert _count(db_path, "SELECT count(*) FROM transactions") == 8


def test_import_both_files_pairs_payment(db_path: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    at.file_uploader[0].set_value(
        [
            ("card.csv", fixture_bytes("chase_card_jan.csv"), "text/csv"),
            ("checking.csv", fixture_bytes("chase_checking_jan.csv"), "text/csv"),
        ]
    ).run()
    # No last4 in these filenames: import stays disabled until both are entered.
    assert at.button(key="import").disabled
    at.text_input[0].input("0000")
    at.text_input[1].input("0000").run()
    at.button(key="import").click().run()
    assert not at.exception
    assert _metrics(at)["New transactions"] == "16"
    assert _metrics(at)["Transfers found"] == "1"
    assert [s.value for s in at.success] == ["No unpaired transfers to review."]


def test_reimport_reports_duplicates(db_path: Path) -> None:
    content = fixture_bytes("chase_card_jan.csv")
    at = AppTest.from_file(PAGE).run()
    at.file_uploader[0].upload("Chase0000_a.csv", content).run()
    at.button(key="import").click().run()
    at.button(key="import").click().run()
    assert _metrics(at)["New transactions"] == "0"
    assert _metrics(at)["Duplicates skipped"] == "8"
    assert any("already imported" in i.value for i in at.info)


def test_unknown_file_shows_error_and_imports_nothing(db_path: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    at.file_uploader[0].upload("Chase1234_x.csv", b"Date,Payee,Amount\n01/01/2026,X,-1\n").run()
    assert not at.exception
    assert any("unrecognized CSV header" in e.value for e in at.error)
    assert at.button(key="import").disabled
