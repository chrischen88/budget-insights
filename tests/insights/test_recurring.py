"""Recurring page read model: status resolution, lapse timing and exact annualized cents.

Synthetic data (tests/synth.py subscriptions_card_csv), amounts hand-computed:
  streaming $15.49 monthly, Jul 3 .. Dec 3 2025 -> next Jan 3 2026, annual 1549 x 12 = 18588
  gym class $12.00 weekly, Oct 1 .. Dec 31 2025 -> next Jan 7 2026, annual 1200 x 52 = 62400
"""

from datetime import date

import duckdb
import pytest

from spendsight.db import repository
from spendsight.insights import recurring
from spendsight.insights.recurring import RecurringCharge
from spendsight.ml.recurring_runner import run_recurring_detection
from spendsight.pipeline import import_and_process
from tests.synth import card_line, subscriptions_card_csv

STREAMING_YEAR, GYM_YEAR = -18588, -62400


@pytest.fixture
def loaded(db: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    import_and_process(db, subscriptions_card_csv(), filename="card.csv", last4="0000")
    return db


def _by_merchant(charges: list[RecurringCharge]) -> dict[str, RecurringCharge]:
    return {c.merchant: c for c in charges}


@pytest.mark.parametrize(("cadence_days", "grace"), [(7, 1), (14, 2), (30, 5), (91, 9), (365, 15)])
def test_grace_is_the_detection_window_slack(cadence_days: int, grace: int) -> None:
    assert recurring.grace_days(cadence_days) == grace


def test_lapse_boundary() -> None:
    due = date(2026, 1, 3)
    assert not recurring.is_lapsed(due, 30, date(2026, 1, 8))
    assert recurring.is_lapsed(due, 30, date(2026, 1, 9))


def test_user_choices_win_over_calendar() -> None:
    overdue = (date(2025, 1, 1), 30, date(2026, 1, 1))
    assert recurring.resolve_status("active", None, *overdue) == "lapsed"
    assert recurring.resolve_status("active", date(2025, 2, 1), *overdue) == "cancelled"
    assert recurring.resolve_status("dismissed", date(2025, 2, 1), *overdue) == "dismissed"
    assert recurring.resolve_status(None, None, date(2026, 1, 3), 30, date(2026, 1, 3)) == (
        "active"
    )


def test_reference_date_is_capped_by_data() -> None:
    assert recurring.reference_date(date(2026, 6, 1), date(2025, 12, 31)) == date(2025, 12, 31)
    assert recurring.reference_date(date(2025, 12, 1), date(2025, 12, 31)) == date(2025, 12, 1)
    assert recurring.reference_date(date(2026, 6, 1), None) == date(2026, 6, 1)


def test_list_and_annualize(loaded: duckdb.DuckDBPyConnection) -> None:
    charges = recurring.list_recurring(loaded, today=date(2026, 1, 5))
    assert [
        (c.merchant, c.cadence, c.typical_cents, c.next_expected, c.status) for c in charges
    ] == [
        ("SYNTH GYM CLASS", "weekly", -1200, date(2026, 1, 7), "active"),
        ("SYNTH STREAMING", "monthly", -1549, date(2026, 1, 3), "active"),
    ]
    assert [c.annualized_cents for c in charges] == [GYM_YEAR, STREAMING_YEAR]
    assert recurring.annualized_total(charges) == GYM_YEAR + STREAMING_YEAR == -80988


def test_stale_import_does_not_lapse_everything(loaded: duckdb.DuckDBPyConnection) -> None:
    """Data ends Dec 31; months later nothing new has been imported, so nothing is known
    to have stopped."""
    charges = recurring.list_recurring(loaded, today=date(2026, 9, 1))
    assert {c.status for c in charges} == {"active"}


def test_newer_data_without_the_charge_lapses_it(db: duckdb.DuckDBPyConnection) -> None:
    extra = [card_line(date(2026, 1, 20), "SYNTH BISTRO", "-30.00")]
    import_and_process(db, subscriptions_card_csv(extra=extra), filename="card.csv", last4="0000")
    charges = _by_merchant(recurring.list_recurring(db, today=date(2026, 2, 1)))
    assert charges["SYNTH STREAMING"].status == "lapsed"
    assert charges["SYNTH GYM CLASS"].status == "lapsed"
    assert recurring.annualized_total(list(charges.values())) == 0


def test_dismiss_cancel_restore(loaded: duckdb.DuckDBPyConnection) -> None:
    today = date(2026, 1, 5)
    ids = {c.merchant: c.series_id for c in recurring.list_recurring(loaded, today)}

    assert repository.dismiss_recurring(loaded, ids["SYNTH GYM CLASS"])
    assert repository.cancel_recurring(loaded, ids["SYNTH STREAMING"], date(2026, 1, 4))
    run_recurring_detection(loaded)  # user choices survive re-detection
    charges = _by_merchant(recurring.list_recurring(loaded, today))
    assert charges["SYNTH GYM CLASS"].status == "dismissed"
    assert charges["SYNTH STREAMING"].status == "cancelled"
    assert charges["SYNTH STREAMING"].cancelled_on == date(2026, 1, 4)
    assert not any(c.is_current for c in charges.values())
    assert recurring.annualized_total(list(charges.values())) == 0

    assert repository.restore_recurring(loaded, ids["SYNTH STREAMING"])
    restored = _by_merchant(recurring.list_recurring(loaded, today))["SYNTH STREAMING"]
    assert (restored.status, restored.cancelled_on) == ("active", None)


def test_actions_on_missing_series(loaded: duckdb.DuckDBPyConnection) -> None:
    assert not repository.dismiss_recurring(loaded, 999)
    assert not repository.cancel_recurring(loaded, 999, date(2026, 1, 1))
    assert not repository.restore_recurring(loaded, 999)


def test_empty_database(db: duckdb.DuckDBPyConnection) -> None:
    assert recurring.list_recurring(db, today=date(2026, 1, 1)) == []
    assert repository.latest_transaction_date(db) is None
