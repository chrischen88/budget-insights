from datetime import date, timedelta

import pytest

from spendsight.ml.recurring import add_months, detect_one, detect_recurring
from tests.synth import recurring_dataset


def monthly(start: date, n: int, cents: int = -1549) -> list[tuple[date, int]]:
    return [(add_months(start, i), cents) for i in range(n)]


@pytest.mark.parametrize(
    ("d", "months", "expected"),
    [
        (date(2026, 1, 31), 1, date(2026, 2, 28)),
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2026, 11, 15), 3, date(2027, 2, 15)),
        (date(2026, 3, 31), 12, date(2027, 3, 31)),
    ],
)
def test_add_months_clamps(d: date, months: int, expected: date) -> None:
    assert add_months(d, months) == expected


def test_monthly_subscription() -> None:
    s = detect_one(1, monthly(date(2026, 1, 31), 4))
    assert s is not None
    assert (s.cadence, s.cadence_days, s.charges) == ("monthly", 30, 4)
    assert s.typical_cents == -1549
    assert s.last_seen == date(2026, 4, 30)
    assert s.next_expected == date(2026, 5, 30)
    assert s.annualized_cents == -1549 * 12


@pytest.mark.parametrize(
    ("step", "cadence", "per_year"),
    [(7, "weekly", 52), (14, "biweekly", 26), (91, "quarterly", 4), (365, "annual", 1)],
)
def test_other_cadences(step: int, cadence: str, per_year: int) -> None:
    charges = [(date(2023, 1, 5) + timedelta(days=step * i), -2000) for i in range(4)]
    s = detect_one(1, charges)
    assert s is not None
    assert s.cadence == cadence
    assert s.annualized_cents == -2000 * per_year


def test_needs_three_charges() -> None:
    assert detect_one(1, monthly(date(2026, 1, 1), 2)) is None


def test_one_skipped_month_is_tolerated() -> None:
    charges = monthly(date(2025, 1, 10), 8)
    del charges[3]
    s = detect_one(1, charges)
    assert s is not None
    assert s.cadence == "monthly"


def test_irregular_gaps_rejected() -> None:
    days = [date(2026, 1, 1) + timedelta(days=d) for d in (0, 9, 40, 47, 90, 101)]
    assert detect_one(1, [(d, -1000) for d in days]) is None


def test_variable_amounts_rejected_small_price_increase_allowed() -> None:
    swinging = [
        (d, c) for (d, _), c in zip(monthly(date(2025, 1, 1), 6), [-5000, -9000] * 3, strict=True)
    ]
    assert detect_one(1, swinging) is None
    raised = monthly(date(2025, 1, 1), 3, -1549) + monthly(date(2025, 4, 1), 3, -1699)
    s = detect_one(1, raised)
    assert s is not None
    assert s.cadence == "monthly"


def test_same_day_charges_do_not_create_zero_gaps() -> None:
    charges = [*monthly(date(2026, 1, 1), 4, -500), (date(2026, 2, 1), -500)]
    s = detect_one(1, charges)
    assert s is not None
    assert s.cadence == "monthly"


def test_amounts_are_exact_integer_cents() -> None:
    s = detect_one(1, monthly(date(2026, 1, 1), 5, -1999))
    assert s is not None
    assert isinstance(s.typical_cents, int)
    assert s.typical_cents == -1999


@pytest.mark.parametrize("seed", range(10))
def test_finds_planted_subscriptions(seed: int) -> None:
    """SPEC §6 acceptance: >= 90% of planted subscriptions found, at the right cadence."""
    charges, truth = recurring_dataset(seed)
    detected = {s.merchant_id: s for s in detect_recurring(charges)}
    found = [t for t in truth if t.merchant_id in detected]
    recall = len(found) / len(truth)
    assert recall >= 0.9, f"seed {seed}: recall {recall:.0%}"
    for t in found:
        assert detected[t.merchant_id].cadence_days == t.cadence_days
    truth_ids = {t.merchant_id for t in truth}
    false_positives = [m for m in detected if m not in truth_ids]
    precision = 1 - len(false_positives) / len(detected)
    assert precision >= 0.9, f"seed {seed}: precision {precision:.0%}"


def test_generator_is_deterministic() -> None:
    a, ta = recurring_dataset(3)
    b, tb = recurring_dataset(3)
    assert a.equals(b)
    assert ta == tb


def test_double_charge_does_not_hide_the_series() -> None:
    charges = [*monthly(date(2026, 1, 1), 4, -1549), (date(2026, 2, 1), -1549)]
    s = detect_one(1, charges)
    assert s is not None
    assert (s.typical_cents, s.charges) == (-1549, 4)
