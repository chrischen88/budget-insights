"""Alerts page against a temporary database (no network)."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from spendsight.db import repository
from spendsight.db.connection import connect
from spendsight.pipeline import import_and_process
from tests.conftest import fixture_bytes
from tests.synth import alerts_card_csv

PAGE = str(Path(__file__).parents[2] / "src/spendsight/app/views/alerts.py")


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
    import_and_process(conn, alerts_card_csv(), filename="card.csv", last4="0000")
    conn.close()
    return db_path


def test_empty_state(db_path: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    assert "Import" in at.info[0].value


def test_no_alerts(db_path: Path) -> None:
    conn = connect(db_path)
    import_and_process(conn, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    conn.close()
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    assert any("No alerts in this period" in c.value for c in at.caption)


def test_default_range_is_last_90_days(loaded: Path) -> None:
    """Data ends Dec 12: the window is Sep 14 - Dec 12, so the Sep 11 duplicate is out."""
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    table = at.dataframe[0].value
    assert list(table.columns) == ["Date", "Merchant", "Amount", "Alert", "Why", "Account"]
    assert list(table["Alert"]) == ["Unusual amount", "Price increase", "Large new merchant"]
    assert list(table["Amount"]) == ["-$450.00", "-$17.99", "-$1,450.00"]
    assert any(c.value.startswith("3 open alerts.") for c in at.caption)


def test_wider_range_and_dismissed_toggle(loaded: Path) -> None:
    conn = connect(loaded)
    [dup] = repository.list_anomalies(conn, date(2025, 9, 1), date(2025, 9, 30))
    repository.set_anomaly_dismissed(conn, dup.transaction_id, dup.kind, True)
    conn.close()

    at = AppTest.from_file(PAGE).run()
    at.date_input(key="alerts-dates").set_value((date(2025, 7, 1), date(2025, 12, 31))).run()
    assert len(at.dataframe[0].value) == 3  # dismissed duplicate hidden
    at.toggle(key="alerts-dismissed").set_value(True).run()
    assert not at.exception
    table = at.dataframe[0].value
    assert list(table["Status"]) == ["Open", "Open", "Open", "Dismissed"]
    assert list(table["Alert"])[-1] == "Possible duplicate"
