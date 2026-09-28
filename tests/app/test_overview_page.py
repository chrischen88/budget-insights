"""End-to-end test of the Overview page against a temporary database (no network)."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from spendsight.db.connection import connect
from spendsight.pipeline import import_and_process
from tests.conftest import fixture_bytes

PAGE = str(Path(__file__).parents[2] / "src/spendsight/app/views/overview.py")


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


def _metrics(at: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


def test_empty_state(db_path: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    assert "Import" in at.info[0].value


def test_kpis_for_full_range(loaded: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    assert _metrics(at) == {"Spent": "$1,677.58", "Income": "$2,500.00", "Net": "$822.42"}


def test_show_transfers_toggle(loaded: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    at.toggle[0].set_value(True).run()
    assert not at.exception
    assert _metrics(at) == {"Spent": "$2,177.58", "Income": "$3,000.00", "Net": "$822.42"}


def test_account_filter(loaded: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    at.multiselect[0].set_value(["chase-card-0000"]).run()
    assert not at.exception
    assert _metrics(at) == {"Spent": "$222.18", "Income": "$0.00", "Net": "-$222.18"}


def test_no_delta_without_prior_data(loaded: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    assert all(not m.delta for m in at.metric)
    assert "No transactions in the previous" in at.caption[0].value


def test_delta_with_prior_data(loaded: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    # Jan 12-21 vs Jan 2-11: both windows have data.
    at.date_input[0].set_value((date(2026, 1, 12), date(2026, 1, 21))).run()
    assert not at.exception
    assert all(m.delta for m in at.metric)
