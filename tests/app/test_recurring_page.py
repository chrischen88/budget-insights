"""Recurring page against a temporary database (no network)."""

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
from tests.synth import subscriptions_card_csv

PAGE = str(Path(__file__).parents[2] / "src/spendsight/app/views/recurring.py")


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
    import_and_process(conn, subscriptions_card_csv(), filename="card.csv", last4="0000")
    conn.close()
    return db_path


def test_empty_state(db_path: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    assert "Import" in at.info[0].value


def test_no_series_found(db_path: Path) -> None:
    conn = connect(db_path)
    import_and_process(conn, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    conn.close()
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    assert any("No recurring charges found" in c.value for c in at.caption)


def test_lists_subscriptions_with_annualized_cost(loaded: Path) -> None:
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    table = at.dataframe[0].value
    assert list(table.columns) == [
        "Merchant",
        "Cadence",
        "Amount",
        "Last charge",
        "Next expected",
        "Annualized",
        "Status",
    ]
    assert list(table["Merchant"]) == ["SYNTH GYM CLASS", "SYNTH STREAMING"]
    assert list(table["Annualized"]) == ["$624.00", "$185.88"]
    assert list(table["Status"]) == ["Active", "Active"]
    metrics = {m.label: m.value for m in at.metric}
    assert metrics == {"Active subscriptions": "2", "Annualized cost": "$809.88"}


def test_set_aside_series_move_to_expander(loaded: Path) -> None:
    conn = connect(loaded)
    ids = {s.merchant: s.id for s in repository.list_recurring_series(conn)}
    repository.cancel_recurring(conn, ids["SYNTH STREAMING"], date(2026, 1, 4))
    conn.close()

    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    assert list(at.dataframe[0].value["Merchant"]) == ["SYNTH GYM CLASS"]
    assert list(at.dataframe[1].value["Status"]) == ["Cancelled Jan 04, 2026"]
    assert {m.label: m.value for m in at.metric}["Annualized cost"] == "$624.00"
    assert "Dismissed and cancelled (1)" in at.expander[0].label
