"""Synthetic transaction generators with known ground truth, for ML tests (seeded)."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from spendsight.ml.anomalies import Charge
from spendsight.ml.recurring import add_months


@dataclass(frozen=True)
class PlantedSeries:
    merchant_id: int
    cadence_days: int  # nominal: 7, 14, 30, 91, 365
    amount_cents: int  # negative


def _monthly_dates(
    start: date, end: date, months: int, rng: random.Random, jitter: int
) -> list[date]:
    out, d = [], start
    while d <= end:
        out.append(d + timedelta(days=rng.randint(0, jitter)))
        d = add_months(d, months)
    return out


def _daily_dates(start: date, end: date, step: int, rng: random.Random, jitter: int) -> list[date]:
    out, d = [], start
    while d <= end:
        out.append(d + timedelta(days=rng.randint(-jitter, jitter)))
        d += timedelta(days=step)
    return out


def recurring_dataset(
    seed: int, start: date = date(2023, 1, 1), end: date = date(2025, 12, 31)
) -> tuple[pd.DataFrame, list[PlantedSeries]]:
    """Three years of outflows: planted subscriptions among realistic noise.

    Returns (charges with merchant_id/date/amount_cents, ground-truth series).
    """
    rng = random.Random(seed)
    rows: list[tuple[int, date, int]] = []
    truth: list[PlantedSeries] = []
    next_id = iter(range(1, 10_000))

    def plant(cadence: int, amount: int, dates: list[date], raises: int = 0) -> None:
        mid = next(next_id)
        truth.append(PlantedSeries(mid, cadence, amount))
        for i, d in enumerate(dates):
            price = amount - (raises if raises and i >= len(dates) // 2 else 0)
            rows.append((mid, d, price))

    # Monthly subscriptions: fixed prices, posting jitter of up to 3 days.
    for _ in range(8):
        day = rng.randint(1, 28)
        plant(30, -rng.randint(500, 3000), _monthly_dates(start.replace(day=day), end, 1, rng, 3))
    # Price increases (~8%) halfway through.
    for _ in range(2):
        amount = -rng.randint(1000, 2000)
        plant(30, amount, _monthly_dates(start, end, 1, rng, 2), raises=round(abs(amount) * 0.08))
    # One that skipped a month, one cancelled a year ago (still a real series).
    skipped = _monthly_dates(start, end, 1, rng, 2)
    del skipped[10]
    plant(30, -1299, skipped)
    plant(30, -899, _monthly_dates(start, date(2024, 12, 31), 1, rng, 2))
    # Month-end billing: Jan 31, Feb 28/29, Mar 31 ...
    plant(30, -1599, _monthly_dates(date(2023, 1, 31), end, 1, rng, 0))
    # Weekly, biweekly, quarterly, annual.
    for _ in range(2):
        plant(7, -rng.randint(3000, 7000), _daily_dates(start, end, 7, rng, 1))
    for _ in range(2):
        plant(14, -rng.randint(8000, 15000), _daily_dates(start, end, 14, rng, 1))
    for _ in range(2):
        plant(91, -rng.randint(5000, 20000), _monthly_dates(start, end, 3, rng, 4))
    for _ in range(2):
        plant(365, -rng.randint(5000, 15000), _monthly_dates(start, end, 12, rng, 5))

    span = (end - start).days

    def noise(dates: list[date], low: int, high: int) -> None:
        mid = next(next_id)
        rows.extend((mid, d, -rng.randint(low, high)) for d in dates)

    # Irregular shopping and dining: random days, widely varying amounts.
    for _ in range(25):
        n = rng.randint(3, 40)
        noise([start + timedelta(days=rng.randint(0, span)) for _ in range(n)], 500, 25000)
    # A daily-ish coffee habit (similar amounts, gaps of 1-3 days).
    d, coffee = start, []
    while d <= end:
        coffee.append(d)
        d += timedelta(days=rng.randint(1, 3))
    noise(coffee, 450, 650)
    # A monthly utility bill whose amount swings with the season (not a subscription).
    mid = next(next_id)
    for i, d in enumerate(_monthly_dates(start, end, 1, rng, 2)):
        rows.append((mid, d, -(6000 + 5000 * ((i % 12) in (0, 1, 6, 7)) + rng.randint(0, 3000))))
    # Merchants seen only twice, a month apart.
    for _ in range(5):
        first = start + timedelta(days=rng.randint(0, span - 40))
        amount = -rng.randint(1000, 5000)
        noise_id = next(next_id)
        rows.extend([(noise_id, first, amount), (noise_id, first + timedelta(days=30), amount)])

    frame = pd.DataFrame(rows, columns=["merchant_id", "date", "amount_cents"])
    return frame.sort_values(["merchant_id", "date"]).reset_index(drop=True), truth


CARD_HEADER = "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"


def card_line(d: date, description: str, amount: str, category: str = "Entertainment") -> str:
    return f"{d:%m/%d/%Y},{d:%m/%d/%Y},{description},{category},Sale,{amount},\n"


def subscriptions_card_csv(*, extra: list[str] | None = None) -> bytes:
    """A Chase card CSV with two subscriptions, amounts chosen for hand-checked totals:
    streaming $15.49 monthly Jul 3 .. Dec 3 2025 (next Jan 3 2026), and a gym class
    $12.00 weekly Oct 1 .. Dec 31 2025 (next Jan 7 2026)."""
    lines = [CARD_HEADER]
    lines += [
        card_line(add_months(date(2025, 7, 3), i), "SYNTH STREAMING", "-15.49") for i in range(6)
    ]
    lines += [
        card_line(date(2025, 10, 1) + timedelta(weeks=i), "SYNTH GYM CLASS", "-12.00")
        for i in range(14)
    ]
    return "".join(lines + (extra or [])).encode()


@dataclass(frozen=True)
class AnomalyDataset:
    charges: list[Charge]
    series_merchants: set[int]
    planted: set[tuple[str, str]]  # (transaction id, kind)
    months: int


def anomaly_dataset(
    seed: int, start: date = date(2024, 1, 1), end: date = date(2025, 12, 31)
) -> AnomalyDataset:
    """Two years of everyday spending with heavy-tailed amounts, merchants that appear over
    time, subscriptions and rent, plus one planted anomaly of each kind (two outliers)."""
    rng = random.Random(seed)
    charges: list[Charge] = []
    ids = iter(range(1, 1_000_000))
    span = (end - start).days
    card, checking = ("card", "Card ...0000"), ("checking", "Checking ...0000")
    categories = {
        1: "Groceries",
        2: "Restaurants",
        3: "Coffee",
        4: "Shopping",
        5: "Gas",
        6: "Rent",
        7: "Streaming",
        8: "Utilities",
        9: "Furniture",
    }

    def add(account: tuple[str, str], merchant: int, category: int, d: date, cents: int) -> str:
        tid = f"t{next(ids)}"
        charges.append(
            Charge(
                tid,
                account[0],
                account[1],
                merchant,
                f"M{merchant}",
                category,
                categories[category],
                d,
                -cents,
            )
        )
        return tid

    def lognormal(median_cents: int, sigma: float) -> int:
        return max(100, round(rng.lognormvariate(math.log(median_cents), sigma)))

    def every(mean_gap: float) -> list[date]:
        out, d = [], start
        while True:
            d += timedelta(days=max(1, round(rng.expovariate(1 / mean_gap))))
            if d > end:
                return out
            out.append(d)

    def pool(first_id: int, size: int, spread: bool) -> list[tuple[int, date]]:
        """Merchants with the date they become available (some appear later on)."""
        return [
            (first_id + k, start + timedelta(days=rng.randint(0, span) if spread and k > 2 else 0))
            for k in range(size)
        ]

    def habit(
        merchants: list[tuple[int, date]], category: int, gap: float, median: int, sigma: float
    ) -> None:
        for d in every(gap):
            merchant = rng.choice([m for m, since in merchants if since <= d])
            add(card, merchant, category, d, lognormal(median, sigma))

    habit(pool(100, 3, False), 1, 3.5, 9000, 0.35)  # groceries
    habit(pool(200, 12, True), 2, 2.5, 2800, 0.4)  # restaurants
    habit(pool(400, 6, True), 5, 7, 4800, 0.2)  # gas
    for d in every(2):
        add(card, 300, 3, d, rng.randint(450, 650))  # coffee
    for d in every(5):  # shopping: a long tail of small and occasionally large orders
        merchant = rng.choice([m for m, since in pool(500, 15, True) if since <= d])
        add(card, merchant, 4, d, lognormal(4500, 0.7))
    series = {600, 601, 602, 603}
    for i in range(24):
        month = add_months(start, i)
        add(checking, 700, 6, month, 180000)  # rent
        add(checking, 800, 8, month + timedelta(days=14), rng.randint(9000, 16000))  # utility
        for k, price in enumerate([1549, 1099, 699, 1999]):
            add(card, 600 + k, 7, month + timedelta(days=3 + k), price)

    planted: set[tuple[str, str]] = set()
    # Outliers: far above the category's usual, at merchants already in use.
    planted.add((add(card, 100, 1, start + timedelta(days=200), 95000), "amount_outlier"))
    planted.add((add(card, 200, 2, start + timedelta(days=500), 60000), "amount_outlier"))
    # A double charge: a restaurant bill repeated the next day.
    dup_day = start + timedelta(days=rng.randint(100, 600))
    add(card, 201, 2, dup_day, 6425)
    planted.add((add(card, 201, 2, dup_day + timedelta(days=1), 6425), "duplicate"))
    # A large first purchase from a new merchant, well after the data begins.
    planted.add((add(card, 900, 9, start + timedelta(days=300), 145000), "new_merchant_large"))
    # A subscription's price rises by 16% a year in: only the first higher charge alerts.
    raise_at = add_months(start, 12) + timedelta(days=30)
    for i, c in enumerate(charges):
        if c.merchant_id == 600 and c.date >= raise_at - timedelta(days=30) and c.date.year == 2025:
            charges[i] = Charge(
                c.id,
                c.account_id,
                c.account,
                c.merchant_id,
                c.merchant,
                c.category_id,
                c.category,
                c.date,
                -1799,
            )
    first_raised = min(
        (c for c in charges if c.merchant_id == 600 and c.amount_cents == -1799),
        key=lambda c: c.date,
    )
    planted.add((first_raised.id, "price_increase"))
    return AnomalyDataset(charges, series, planted, months=24)


def alerts_card_csv() -> bytes:
    """A Chase card CSV (data starts Jul 3 2025) with one anomaly of each kind:
      price_increase      SYNTH STREAMING $15.49 Jul-Oct, $17.99 from Nov 3
      amount_outlier      SYNTH GROCER: eight charges $70-$110 (median $90, MAD $10),
                          then $450.00 on Dec 12
      duplicate           SYNTH BISTRO $64.25 on Sep 10 and again Sep 11
      new_merchant_large  SYNTH FURNITURE $1,450.00 on Oct 20 (after the 60-day warm-up)
    Grocer gaps are irregular (median 20.5 days) so it is not a recurring series."""
    lines = [CARD_HEADER]
    for i, price in enumerate(["-15.49"] * 4 + ["-17.99"] * 2):
        lines.append(card_line(add_months(date(2025, 7, 3), i), "SYNTH STREAMING", price))
    grocer = [
        ("07/05", "-70.00"),
        ("07/19", "-80.00"),
        ("08/02", "-80.00"),
        ("08/30", "-90.00"),
        ("09/06", "-90.00"),
        ("10/11", "-100.00"),
        ("11/01", "-100.00"),
        ("11/22", "-110.00"),
        ("12/12", "-450.00"),
    ]
    for day, amount in grocer:
        d = date(2025, int(day[:2]), int(day[3:]))
        lines.append(card_line(d, "SYNTH GROCER", amount, "Groceries"))
    for d in (date(2025, 9, 10), date(2025, 9, 11)):
        lines.append(card_line(d, "SYNTH BISTRO", "-64.25", "Food & Drink"))
    lines.append(card_line(date(2025, 10, 20), "SYNTH FURNITURE", "-1450.00", "Home"))
    return "".join(lines).encode()
