"""Recurring-charge detection (SPEC.md §6). Pure: charges in, series out.

For each merchant with at least MIN_CHARGES charge days, the median gap between
charge dates is matched to a cadence bucket. A series is accepted when most gaps fit the
bucket (a skipped period, i.e. roughly twice the cadence, is tolerated) and amounts are
stable (coefficient of variation < MAX_AMOUNT_CV). Explainable by design: every accepted
series can be justified from its dates and amounts.
"""

from __future__ import annotations

import calendar
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Literal

import pandas as pd

Cadence = Literal["weekly", "biweekly", "monthly", "quarterly", "annual"]

MIN_CHARGES = 3
MAX_AMOUNT_CV = 0.15
MIN_FITTING_GAPS = 0.75  # share of gaps that must fit the cadence (or be a skipped period)


@dataclass(frozen=True)
class CadenceBucket:
    name: Cadence
    days: int  # nominal cadence stored in recurring_series.cadence_days
    low: int  # inclusive gap window in days
    high: int
    months: int  # calendar step for next_expected (0 = use days)
    per_year: int  # charges per year, for exact annualized cost


BUCKETS: tuple[CadenceBucket, ...] = (
    CadenceBucket("weekly", 7, 6, 8, 0, 52),
    CadenceBucket("biweekly", 14, 12, 16, 0, 26),
    CadenceBucket("monthly", 30, 26, 35, 1, 12),
    CadenceBucket("quarterly", 91, 82, 100, 3, 4),
    CadenceBucket("annual", 365, 350, 380, 12, 1),
)
BUCKET_BY_DAYS = {b.days: b for b in BUCKETS}


@dataclass(frozen=True)
class DetectedSeries:
    merchant_id: int
    cadence: Cadence
    cadence_days: int
    typical_cents: int  # median charge, negative (an outflow)
    last_seen: date
    next_expected: date
    charges: int

    @property
    def annualized_cents(self) -> int:
        return self.typical_cents * BUCKET_BY_DAYS[self.cadence_days].per_year


def add_months(d: date, months: int) -> date:
    """Same day `months` later, clamped to the month's last day (Jan 31 + 1 -> Feb 28/29)."""
    month_index = d.month - 1 + months
    year, month = d.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def next_after(last: date, bucket: CadenceBucket) -> date:
    return add_months(last, bucket.months) if bucket.months else last + timedelta(bucket.days)


def _bucket_for(gap: float) -> CadenceBucket | None:
    return next((b for b in BUCKETS if b.low <= gap <= b.high), None)


def _fits(gap: int, bucket: CadenceBucket) -> bool:
    """Within the window, or one skipped period (about twice the cadence)."""
    return bucket.low <= gap <= bucket.high or 2 * bucket.low <= gap <= 2 * bucket.high


def one_per_day(charges: Iterable[tuple[date, int]]) -> dict[date, int]:
    """Same-day charges count once: keep the one closest to the merchant's median amount,
    so a double charge (an anomaly, flagged elsewhere) doesn't hide the series."""
    charge_list = list(charges)
    if not charge_list:
        return {}
    median = statistics.median(abs(c) for _, c in charge_list)
    by_day: dict[date, int] = {}
    for day, cents in charge_list:
        if day not in by_day or abs(abs(cents) - median) < abs(abs(by_day[day]) - median):
            by_day[day] = cents
    return by_day


def detect_one(merchant_id: int, charges: Iterable[tuple[date, int]]) -> DetectedSeries | None:
    """One merchant's outflows as (date, amount_cents<0)."""
    by_day = one_per_day(charges)
    days = sorted(by_day)
    if len(days) < MIN_CHARGES:
        return None
    gaps = [(b - a).days for a, b in pairwise(days)]
    bucket = _bucket_for(statistics.median(gaps))
    if bucket is None:
        return None
    if sum(_fits(g, bucket) for g in gaps) / len(gaps) < MIN_FITTING_GAPS:
        return None
    amounts = [abs(by_day[d]) for d in days]
    mean = statistics.fmean(amounts)
    if mean == 0 or statistics.pstdev(amounts) / mean >= MAX_AMOUNT_CV:
        return None
    typical = -int(statistics.median_low(amounts))
    return DetectedSeries(
        merchant_id=merchant_id,
        cadence=bucket.name,
        cadence_days=bucket.days,
        typical_cents=typical,
        last_seen=days[-1],
        next_expected=next_after(days[-1], bucket),
        charges=len(days),
    )


def detect_recurring(charges: pd.DataFrame) -> list[DetectedSeries]:
    """Columns: merchant_id, date, amount_cents (outflows only). One series per merchant."""
    by_merchant: dict[int, list[tuple[date, int]]] = {}
    for merchant_id, day, cents in zip(
        charges["merchant_id"], charges["date"], charges["amount_cents"], strict=True
    ):
        by_merchant.setdefault(int(merchant_id), []).append((day, int(cents)))
    out = []
    for merchant_id in sorted(by_merchant):
        series = detect_one(merchant_id, by_merchant[merchant_id])
        if series is not None:
            out.append(series)
    return out
