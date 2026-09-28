from datetime import date

import duckdb
import pandas as pd
import pytest

from spendsight.db import repository
from spendsight.ml.recurring import add_months
from spendsight.ml.recurring_runner import run_recurring_detection
from spendsight.pipeline import import_and_process

CARD_HEADER = "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
CHECKING_HEADER = "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"


def _card_csv() -> bytes:
    """Six months: a $15.49 streaming subscription, irregular dining, one payment."""
    lines = [CARD_HEADER]
    for i in range(6):
        d = add_months(date(2025, 7, 3), i)
        lines.append(f"{d:%m/%d/%Y},{d:%m/%d/%Y},SYNTH STREAMING,Entertainment,Sale,-15.49,\n")
    for day, amount in [
        ("07/09", "-23.10"),
        ("08/30", "-61.75"),
        ("09/02", "-8.40"),
        ("11/21", "-45.00"),
    ]:
        lines.append(f"{day}/2025,{day}/2025,SYNTH BISTRO,Food & Drink,Sale,{amount},\n")
    return "".join(lines).encode()


def _checking_csv() -> bytes:
    """Monthly autopay to a card that is never imported: a transfer, not a subscription."""
    lines = [CHECKING_HEADER]
    for i in range(6):
        d = add_months(date(2025, 7, 15), i)
        lines.append(
            f"DEBIT,{d:%m/%d/%Y},CHASE CREDIT CRD AUTOPAY PPD ID: 0000000000,-500.00,"
            "ACH_DEBIT,1000.00,,\n"
        )
    return "".join(lines).encode()


@pytest.fixture
def loaded(db: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    import_and_process(db, _card_csv(), filename="card.csv", last4="0000")
    import_and_process(db, _checking_csv(), filename="chk.csv", last4="0000")
    return db


def _series(db: duckdb.DuckDBPyConnection) -> list[tuple[object, ...]]:
    return db.execute(
        "SELECT m.normalized_key, s.cadence_days, s.typical_cents, s.last_seen, "
        "s.next_expected, s.status FROM recurring_series s "
        "JOIN merchants m ON m.id = s.merchant_id ORDER BY m.normalized_key"
    ).fetchall()


def test_import_detects_subscription_only(loaded: duckdb.DuckDBPyConnection) -> None:
    assert _series(loaded) == [
        ("SYNTH STREAMING", 30, -1549, date(2025, 12, 3), date(2026, 1, 3), "active")
    ]


def test_rerun_is_stable(loaded: duckdb.DuckDBPyConnection) -> None:
    before = _series(loaded)
    assert run_recurring_detection(loaded) == 1
    assert _series(loaded) == before


def test_user_dismissal_and_cancellation_survive_redetection(
    loaded: duckdb.DuckDBPyConnection,
) -> None:
    loaded.execute("UPDATE recurring_series SET status = 'dismissed'")
    run_recurring_detection(loaded)
    assert _series(loaded)[0][-1] == "dismissed"
    loaded.execute(
        "UPDATE recurring_series SET status = 'active', cancelled_on = DATE '2025-12-10'"
    )
    run_recurring_detection(loaded)
    assert loaded.execute("SELECT cancelled_on FROM recurring_series").fetchone() == (
        date(2025, 12, 10),
    )


def test_series_no_longer_detected_is_removed_unless_user_touched_it(
    loaded: duckdb.DuckDBPyConnection,
) -> None:
    empty = pd.DataFrame(
        columns=["merchant_id", "cadence_days", "typical_cents", "last_seen", "next_expected"]
    )
    loaded.execute("UPDATE recurring_series SET cancelled_on = DATE '2025-12-10'")
    repository.save_recurring_series(loaded, empty)
    assert len(_series(loaded)) == 1  # cancelled by the user: kept
    loaded.execute("UPDATE recurring_series SET cancelled_on = NULL")
    repository.save_recurring_series(loaded, empty)
    assert _series(loaded) == []


def test_pipeline_reports_series(db: duckdb.DuckDBPyConnection) -> None:
    summary = import_and_process(db, _card_csv(), filename="card.csv", last4="0000")
    assert summary.recurring_series == 1
