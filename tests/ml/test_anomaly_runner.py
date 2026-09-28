"""Anomaly detection through the real import pipeline (tests/synth.py alerts_card_csv)."""

from datetime import date

import duckdb
import pytest

from spendsight.db import repository
from spendsight.enrich.recategorize import recategorize
from spendsight.ml.anomaly_runner import run_anomaly_detection
from spendsight.ml.recurring_runner import run_recurring_detection
from spendsight.pipeline import import_and_process
from tests.synth import alerts_card_csv

ALL_TIME = (date(2025, 1, 1), date(2025, 12, 31))

EXPECTED = [
    (
        date(2025, 12, 12),
        "amount_outlier",
        "$450.00 at SYNTH GROCER is more than 5 times the typical Groceries charge ($90.00).",
    ),
    (date(2025, 11, 3), "price_increase", "SYNTH STREAMING charged $17.99, up from $15.49 (+16%)."),
    (
        date(2025, 10, 20),
        "new_merchant_large",
        "First charge ever from SYNTH FURNITURE, and it's $1,450.00.",
    ),
    (
        date(2025, 9, 11),
        "duplicate",
        "Same $64.25 charge from SYNTH BISTRO on Sep 10, 2025 and Sep 11, 2025 on "
        "Chase Card ••0000. Possible double charge.",
    ),
]


@pytest.fixture
def loaded(db: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    summary = import_and_process(db, alerts_card_csv(), filename="card.csv", last4="0000")
    assert summary.anomalies == 4
    return db


def _alerts(db: duckdb.DuckDBPyConnection, **kwargs: bool) -> list[repository.StoredAnomaly]:
    return repository.list_anomalies(db, *ALL_TIME, **kwargs)


def _by_kind(db: duckdb.DuckDBPyConnection) -> dict[str, repository.StoredAnomaly]:
    return {a.kind: a for a in _alerts(db, include_dismissed=True)}


def test_import_finds_one_of_each_kind(loaded: duckdb.DuckDBPyConnection) -> None:
    assert [(a.date, a.kind, a.explanation) for a in _alerts(loaded)] == EXPECTED
    assert [a.amount_cents for a in _alerts(loaded)] == [-45000, -1799, -145000, -6425]


def test_rerun_and_reimport_are_stable(loaded: duckdb.DuckDBPyConnection) -> None:
    before = _alerts(loaded)
    assert run_anomaly_detection(loaded) == 4
    import_and_process(loaded, alerts_card_csv(), filename="card.csv", last4="0000")
    assert _alerts(loaded) == before


def test_dismissal_survives_redetection(loaded: duckdb.DuckDBPyConnection) -> None:
    dup = _by_kind(loaded)["duplicate"]
    assert repository.set_anomaly_dismissed(loaded, dup.transaction_id, "duplicate", True)
    run_anomaly_detection(loaded)
    assert [a.kind for a in _alerts(loaded)] == [
        "amount_outlier",
        "price_increase",
        "new_merchant_large",
    ]
    assert _by_kind(loaded)["duplicate"].dismissed
    assert repository.set_anomaly_dismissed(loaded, dup.transaction_id, "duplicate", False)
    assert len(_alerts(loaded)) == 4


def test_date_range_filter(loaded: duckdb.DuckDBPyConnection) -> None:
    alerts = repository.list_anomalies(loaded, date(2025, 10, 1), date(2025, 11, 30))
    assert [a.kind for a in alerts] == ["price_increase", "new_merchant_large"]


def test_recategorizing_clears_a_stale_outlier(loaded: duckdb.DuckDBPyConnection) -> None:
    """Also a regression test: updating a transaction that an anomaly references (DuckDB
    foreign keys, SPEC.md §4) must work."""
    outlier = _by_kind(loaded)["amount_outlier"]
    shopping = next(c.id for c in repository.list_categories(loaded) if c.name == "Shopping")
    recategorize(loaded, outlier.transaction_id, shopping)
    run_anomaly_detection(loaded)
    assert "amount_outlier" not in _by_kind(loaded)


def test_dismissed_series_has_no_price_alerts(loaded: duckdb.DuckDBPyConnection) -> None:
    [series] = [
        s for s in repository.list_recurring_series(loaded) if s.merchant == "SYNTH STREAMING"
    ]
    repository.dismiss_recurring(loaded, series.id)
    run_recurring_detection(loaded)
    run_anomaly_detection(loaded)
    assert "price_increase" not in _by_kind(loaded)


def test_missing_alert(loaded: duckdb.DuckDBPyConnection) -> None:
    assert not repository.set_anomaly_dismissed(loaded, "nope", "duplicate", True)


def test_integrity_holds(loaded: duckdb.DuckDBPyConnection) -> None:
    assert repository.integrity_problems(loaded) == []
