"""Anomaly detection (SPEC.md §6). Pure: outflows in, anomalies out.

Four explainable checks, each producing a plain-English explanation from a template
(no LLM):
  amount_outlier      far above the usual charge for its category (robust z-score)
  duplicate           same account, merchant and amount within a few days
  new_merchant_large  a large first-ever charge from a merchant
  price_increase      a subscription charging more than it used to

Inputs are outflows only, with transfers and excluded rows already removed. Amounts in
comparisons are integer cents; only scores (not money) are floats.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable, Collection, Hashable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Literal

from spendsight.ml.recurring import one_per_day
from spendsight.money import format_cents

AnomalyKind = Literal["amount_outlier", "duplicate", "new_merchant_large", "price_increase"]

MAD_TO_Z = 0.6745  # modified z-score (Iglewicz & Hoaglin): 0.6745 * (x - median) / MAD


@dataclass(frozen=True)
class Charge:
    """One outflow, with the display names its explanation needs."""

    id: str
    account_id: str
    account: str
    merchant_id: int
    merchant: str
    category_id: int | None
    category: str | None
    date: date
    amount_cents: int  # negative


@dataclass(frozen=True)
class AnomalyConfig:
    outlier_z: float = 3.5
    outlier_min_samples: int = 8  # charges in the category before it has a "usual"
    outlier_min_ratio: int = 2  # and at least this multiple of the category median
    outlier_min_excess_cents: int = 5000  # and at least this far above it
    duplicate_window_days: int = 3
    duplicate_min_cents: int = 2000
    new_merchant_min_cents: int = 20000
    new_merchant_warmup_days: int = 60  # after the account's first transaction
    price_increase_pct: int = 5
    price_baseline_charges: int = 3  # compared with the highest of this many before


@dataclass(frozen=True)
class Anomaly:
    transaction_id: str
    kind: AnomalyKind
    # outlier: modified z; duplicate: 1; new merchant: amount / threshold;
    # price increase: fractional rise.
    score: float
    explanation: str


def _group[K: Hashable](
    charges: Iterable[Charge], key: Callable[[Charge], K]
) -> dict[K, list[Charge]]:
    groups: dict[K, list[Charge]] = defaultdict(list)
    for c in charges:
        groups[key(c)].append(c)
    return groups


def _money(cents: int) -> str:
    return format_cents(abs(cents), signed=False)


def _day(d: date) -> str:
    return f"{d:%b %d, %Y}"


def _rounded_pct(new: int, old: int) -> int:
    """Percent rise from old to new (both positive cents), rounded half up."""
    return (200 * (new - old) + old) // (2 * old)


def amount_outliers(charges: list[Charge], config: AnomalyConfig) -> list[Anomaly]:
    out = []
    categorized = (c for c in charges if c.category_id is not None)
    for group in _group(categorized, lambda c: c.category_id).values():
        if len(group) < config.outlier_min_samples:
            continue
        amounts = [abs(c.amount_cents) for c in group]
        median = statistics.median_low(amounts)
        mad = statistics.median(abs(a - median) for a in amounts)
        if mad == 0 or median == 0:
            continue  # a fixed amount (rent): nothing is "usual variation"
        for c in group:
            amount = abs(c.amount_cents)
            z = MAD_TO_Z * (amount - median) / mad
            if (
                z > config.outlier_z
                and amount >= config.outlier_min_ratio * median
                and amount - median >= config.outlier_min_excess_cents
            ):
                out.append(
                    Anomaly(
                        c.id,
                        "amount_outlier",
                        round(z, 2),
                        f"{_money(amount)} at {c.merchant} is more than {amount // median} times "
                        f"the typical {c.category} charge ({_money(median)}).",
                    )
                )
    return out


def duplicates(charges: list[Charge], config: AnomalyConfig) -> list[Anomaly]:
    """Flags the later charge of each close pair; the first one is presumed genuine."""
    out = []
    window = timedelta(days=config.duplicate_window_days)
    eligible = (c for c in charges if abs(c.amount_cents) >= config.duplicate_min_cents)
    for group in _group(eligible, lambda c: (c.account_id, c.merchant_id, c.amount_cents)).values():
        for earlier, later in pairwise(sorted(group, key=lambda c: (c.date, c.id))):
            if later.date - earlier.date > window:
                continue
            when = (
                f"twice on {_day(later.date)}"
                if later.date == earlier.date
                else f"on {_day(earlier.date)} and {_day(later.date)}"
            )
            out.append(
                Anomaly(
                    later.id,
                    "duplicate",
                    1.0,
                    f"Same {_money(later.amount_cents)} charge from {later.merchant} {when} "
                    f"on {later.account}. Possible double charge.",
                )
            )
    return out


def new_large_merchants(charges: list[Charge], config: AnomalyConfig) -> list[Anomaly]:
    """A merchant's first charge only, and only once the account has enough history that
    "first" means new rather than "before the imported data begins"."""
    out = []
    account_start = {
        account: min(c.date for c in group)
        for account, group in _group(charges, lambda c: c.account_id).items()
    }
    warmup = timedelta(days=config.new_merchant_warmup_days)
    for group in _group(charges, lambda c: c.merchant_id).values():
        first = min(group, key=lambda c: (c.date, c.amount_cents, c.id))
        amount = abs(first.amount_cents)
        if amount < config.new_merchant_min_cents:
            continue
        if first.date < account_start[first.account_id] + warmup:
            continue
        out.append(
            Anomaly(
                first.id,
                "new_merchant_large",
                round(amount / config.new_merchant_min_cents, 2),
                f"First charge ever from {first.merchant}, and it's {_money(amount)}.",
            )
        )
    return out


def price_increases(
    charges: list[Charge], series_merchants: Collection[int], config: AnomalyConfig
) -> list[Anomaly]:
    """Recurring charges above the highest of the previous few, so a price that wobbles
    back and forth isn't flagged each time it goes up."""
    out = []
    in_series = (c for c in charges if c.merchant_id in series_merchants)
    for group in _group(in_series, lambda c: c.merchant_id).values():
        by_day = one_per_day((c.date, c.amount_cents) for c in group)
        ids = {(c.date, c.amount_cents): c.id for c in group}
        days = sorted(by_day)
        for i in range(1, len(days)):
            previous = days[max(0, i - config.price_baseline_charges) : i]
            baseline = max(abs(by_day[d]) for d in previous)
            amount = abs(by_day[days[i]])
            if amount * 100 <= baseline * (100 + config.price_increase_pct):
                continue
            out.append(
                Anomaly(
                    ids[(days[i], by_day[days[i]])],
                    "price_increase",
                    round((amount - baseline) / baseline, 4),
                    f"{group[0].merchant} charged {_money(amount)}, up from {_money(baseline)} "
                    f"(+{_rounded_pct(amount, baseline)}%).",
                )
            )
    return out


def detect_anomalies(
    charges: list[Charge],
    series_merchants: Collection[int],
    config: AnomalyConfig | None = None,
) -> list[Anomaly]:
    """series_merchants: merchants with a recurring series the user hasn't dismissed.
    At most one anomaly per (transaction, kind)."""
    config = config or AnomalyConfig()
    found = [
        *amount_outliers(charges, config),
        *duplicates(charges, config),
        *new_large_merchants(charges, config),
        *price_increases(charges, series_merchants, config),
    ]
    return sorted(found, key=lambda a: (a.transaction_id, a.kind))
