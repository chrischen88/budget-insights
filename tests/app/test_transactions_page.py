"""Transactions page against a temporary database (no network)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from spendsight.db.connection import connect
from spendsight.pipeline import import_and_process
from tests.conftest import fixture_bytes

PAGE = str(Path(__file__).parents[2] / "src/spendsight/app/views/transactions.py")


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "app.duckdb"
    monkeypatch.setenv("SPENDSIGHT_DB_PATH", str(path))
    monkeypatch.setenv("SPENDSIGHT_LOCAL_ONLY", "true")
    st.cache_resource.clear()
    yield path
    st.cache_resource.clear()


@pytest.fixture
def loaded(db_path: Path) -> Path:
    conn = connect(db_path)
    import_and_process(conn, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    import_and_process(
        conn, fixture_bytes("chase_checking_jan.csv"), filename="k.csv", last4="0000"
    )
    conn.close()
    return db_path


def _table_rows(at: AppTest) -> int:
    return len(at.dataframe[0].value)


def test_empty_state(db_path: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    assert "Import" in at.info[0].value


def test_lists_transactions_without_transfers(loaded: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    assert _table_rows(at) == 14
    assert list(at.dataframe[0].value.columns) == [
        "Date",
        "Account",
        "Merchant",
        "Amount",
        "Category",
        "Source",
        "Description",
    ]


def test_search_and_category_filters(loaded: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    at.text_input(key="txn-search").input("coffee").run()
    assert _table_rows(at) == 2
    at.text_input(key="txn-search").input("").run()
    at.selectbox(key="txn-category").set_value("Uncategorized").run()
    assert not at.exception
    assert _table_rows(at) == 4


def test_no_match_message(loaded: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    at.text_input(key="txn-search").input("nothing like this").run()
    assert any("No transactions match" in c.value for c in at.caption)
