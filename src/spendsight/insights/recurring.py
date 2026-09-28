"""Recurring charges for the Recurring page (SPEC.md §9). All amounts are integer cents.

Detection (ml/recurring.py) stores series; this module decides how each one reads today.
"Lapsed" is not stored: a series lapses once its next charge is overdue by more than the
cadence window allows. Overdue is measured against the data, not just the calendar: the
reference date is `today` or the newest imported transaction, whichever is earlier, so a
stale import doesn't make every subscription look lapsed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import duckdb

from spendsight.db import repository
from spendsight.ml.recurring import BUCKET_BY_DAYS, Cadence

Status = Literal["active", "lapsed", "dismissed", "cancelled"]


@dataclass(frozen=True)
class RecurringCharge:
    series_id: int
    merchant: str
    cadence: Cadence
    cadence_days: int
    typical_cents: int  # negative (an outflow)
    last_seen: date
    next_expected: date
    status: Status
    cancelled_on: date | None

    @property
    def annualized_cents(self) -> int:
        return self.typical_cents * BUCKET_BY_DAYS[self.cadence_days].per_year

    @property
    def is_current(self) -> bool:
        """Still expected to charge: shown in the main list and counted in totals."""
        return self.status in ("active", "lapsed")


def grace_days(cadence_days: int) -> int:
    """How late a charge may post before the series counts as lapsed: the slack in the
    detection window (e.g. monthly: nominal 30, window up to 35 -> 5 days)."""
    bucket = BUCKET_BY_DAYS[cadence_days]
    return bucket.high - bucket.days


def is_lapsed(next_expected: date, cadence_days: int, as_of: date) -> bool:
    return as_of > next_expected + timedelta(days=grace_days(cadence_days))


def resolve_status(
    stored: str | None,
    cancelled_on: date | None,
    next_expected: date,
    cadence_days: int,
    as_of: date,
) -> Status:
    """User choices win over the calendar: dismissed, then cancelled, then lapsed/active."""
    if stored == "dismissed":
        return "dismissed"
    if cancelled_on is not None:
        return "cancelled"
    return "lapsed" if is_lapsed(next_expected, cadence_days, as_of) else "active"


def to_charges(series: list[repository.StoredSeries], as_of: date) -> list[RecurringCharge]:
    """Stored series -> display rows, largest annual cost first."""
    out = [
        RecurringCharge(
            series_id=s.id,
            merchant=s.merchant,
            cadence=BUCKET_BY_DAYS[s.cadence_days].name,
            cadence_days=s.cadence_days,
            typical_cents=s.typical_cents,
            last_seen=s.last_seen,
            next_expected=s.next_expected,
            status=resolve_status(s.status, s.cancelled_on, s.next_expected, s.cadence_days, as_of),
            cancelled_on=s.cancelled_on,
        )
        for s in series
    ]
    # Outflows are negative, so ascending puts the largest yearly cost first.
    return sorted(out, key=lambda c: (c.annualized_cents, c.merchant))


def reference_date(today: date, data_through: date | None) -> date:
    return today if data_through is None else min(today, data_through)


def list_recurring(conn: duckdb.DuckDBPyConnection, today: date) -> list[RecurringCharge]:
    as_of = reference_date(today, repository.latest_transaction_date(conn))
    return to_charges(repository.list_recurring_series(conn), as_of)


def annualized_total(charges: list[RecurringCharge]) -> int:
    """Yearly cost of the subscriptions still running (active only), negative cents."""
    return sum(c.annualized_cents for c in charges if c.status == "active")
