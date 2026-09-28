"""Anomaly checks: boundaries, explanation text, and planted ground truth (tests/synth.py)."""

from datetime import date, timedelta

import pytest

from spendsight.ml.anomalies import (
    AnomalyConfig,
    Charge,
    amount_outliers,
    detect_anomalies,
    duplicates,
    new_large_merchants,
    price_increases,
)
from tests.synth import anomaly_dataset

CONFIG = AnomalyConfig()
D0 = date(2025, 1, 1)
_ids = iter(range(1, 1_000_000))


def charge(
    cents: int,
    day: int = 0,
    *,
    merchant: int = 1,
    category: int | None = 1,
    account: str = "card",
) -> Charge:
    return Charge(
        id=f"t{next(_ids)}",
        account_id=account,
        account="Card ...0000" if account == "card" else "Checking ...0000",
        merchant_id=merchant,
        merchant=f"Merchant {merchant}",
        category_id=category,
        category=None if category is None else "Groceries",
        date=D0 + timedelta(days=day),
        amount_cents=-cents,
    )


# Nine grocery charges: median $90.00, MAD $10.00 -> z > 3.5 needs > $90 + $51.89.
BASELINE = [7000, 8000, 8000, 9000, 9000, 9000, 10000, 10000, 11000]


def _groceries(extra: int) -> tuple[list[Charge], Charge]:
    rows = [charge(c, i, merchant=i % 3 + 1) for i, c in enumerate(BASELINE)]
    odd = charge(extra, 30, merchant=2)
    return [*rows, odd], odd


class TestAmountOutlier:
    def test_flags_with_explanation(self) -> None:
        rows, odd = _groceries(45000)
        [found] = amount_outliers(rows, CONFIG)
        assert found.transaction_id == odd.id
        assert found.explanation == (
            "$450.00 at Merchant 2 is more than 5 times the typical Groceries charge ($90.00)."
        )
        assert found.score == pytest.approx(0.6745 * 36000 / 1000, abs=0.01)

    def test_needs_twice_the_median(self) -> None:
        # z = 0.6745 * 8900 / 1000 = 6.0 but $179.00 < 2 x $90.00: not flagged.
        rows, _ = _groceries(17900)
        assert amount_outliers(rows, CONFIG) == []
        rows, _ = _groceries(18000)
        assert len(amount_outliers(rows, CONFIG)) == 1

    def test_needs_absolute_excess(self) -> None:
        """Coffee: median $5.00, a $30.00 order is 6x but only $25 above usual."""
        rows = [charge(c, i) for i, c in enumerate([450, 480, 500, 500, 500, 520, 550, 600])]
        rows.append(charge(3000, 20))
        assert amount_outliers(rows, CONFIG) == []

    def test_needs_enough_history_and_variation(self) -> None:
        rows, _ = _groceries(45000)
        assert amount_outliers(rows[:6] + rows[-1:], CONFIG) == []  # 7 charges incl. odd one
        assert len(amount_outliers(rows[:7] + rows[-1:], CONFIG)) == 1  # 8: enough
        rent = [charge(180000, 30 * i) for i in range(12)] + [charge(360000, 400)]
        assert amount_outliers(rent, CONFIG) == []  # fixed amount: MAD is 0

    def test_uncategorized_is_skipped(self) -> None:
        rows = [charge(c, i, category=None) for i, c in enumerate([*BASELINE, 45000])]
        assert amount_outliers(rows, CONFIG) == []


class TestDuplicate:
    def test_flags_the_later_charge(self) -> None:
        first, second = charge(5420, 0), charge(5420, 2)
        [found] = duplicates([second, first], CONFIG)
        assert found.transaction_id == second.id
        assert found.explanation == (
            "Same $54.20 charge from Merchant 1 on Jan 01, 2025 and Jan 03, 2025 on "
            "Card ...0000. Possible double charge."
        )

    def test_same_day_wording(self) -> None:
        [found] = duplicates([charge(5420, 5), charge(5420, 5)], CONFIG)
        assert "twice on Jan 06, 2025" in found.explanation

    def test_window_boundary(self) -> None:
        assert len(duplicates([charge(5420, 0), charge(5420, 3)], CONFIG)) == 1
        assert duplicates([charge(5420, 0), charge(5420, 4)], CONFIG) == []

    def test_must_match_account_merchant_and_amount(self) -> None:
        base = charge(5420, 0)
        assert duplicates([base, charge(5420, 1, account="checking")], CONFIG) == []
        assert duplicates([base, charge(5420, 1, merchant=2)], CONFIG) == []
        assert duplicates([base, charge(5421, 1)], CONFIG) == []

    def test_small_repeats_are_ignored(self) -> None:
        assert duplicates([charge(1999, 0), charge(1999, 0)], CONFIG) == []
        assert len(duplicates([charge(2000, 0), charge(2000, 0)], CONFIG)) == 1

    def test_three_in_a_row_flags_two(self) -> None:
        rows = [charge(5420, 0), charge(5420, 1), charge(5420, 2)]
        assert [a.transaction_id for a in duplicates(rows, CONFIG)] == [rows[1].id, rows[2].id]


class TestNewLargeMerchant:
    def _history(self) -> list[Charge]:
        return [charge(1000, 0, merchant=9)]  # the account's data starts on Jan 01

    def test_flags_first_charge_after_warmup(self) -> None:
        new = charge(145000, 60, merchant=2)
        later = charge(150000, 90, merchant=2)
        [found] = new_large_merchants([*self._history(), new, later], CONFIG)
        assert found.transaction_id == new.id
        assert found.explanation == "First charge ever from Merchant 2, and it's $1,450.00."

    def test_warmup_boundary(self) -> None:
        early = charge(145000, 59, merchant=2)
        assert new_large_merchants([*self._history(), early], CONFIG) == []

    def test_threshold_and_first_only(self) -> None:
        assert new_large_merchants([*self._history(), charge(19999, 90, merchant=2)], CONFIG) == []
        assert len(new_large_merchants([*self._history(), charge(20000, 90, merchant=2)], CONFIG))
        # A large second charge from a known merchant is not "new".
        rows = [*self._history(), charge(5000, 70, merchant=2), charge(90000, 90, merchant=2)]
        assert new_large_merchants(rows, CONFIG) == []

    def test_warmup_is_per_account(self) -> None:
        """A checking account imported later has its own warm-up."""
        rows = [*self._history(), charge(1000, 300, merchant=8, account="checking")]
        rows.append(charge(50000, 320, merchant=2, account="checking"))
        assert new_large_merchants(rows, CONFIG) == []


class TestPriceIncrease:
    def _monthly(self, prices: list[int], merchant: int = 5) -> list[Charge]:
        return [charge(p, 30 * i, merchant=merchant) for i, p in enumerate(prices)]

    def test_flags_first_higher_charge(self) -> None:
        rows = self._monthly([1549, 1549, 1549, 1799, 1799, 1799])
        [found] = price_increases(rows, {5}, CONFIG)
        assert found.transaction_id == rows[3].id
        assert found.explanation == "Merchant 5 charged $17.99, up from $15.49 (+16%)."
        assert found.score == pytest.approx(250 / 1549, abs=1e-4)

    def test_five_percent_boundary(self) -> None:
        assert price_increases(self._monthly([10000, 10000, 10500]), {5}, CONFIG) == []
        assert len(price_increases(self._monthly([10000, 10000, 10501]), {5}, CONFIG)) == 1

    def test_wobble_is_not_reflagged(self) -> None:
        """Up once, then back and forth under the recent high: one alert."""
        rows = self._monthly([1000, 1000, 1100, 1000, 1100, 1000, 1100])
        assert [a.transaction_id for a in price_increases(rows, {5}, CONFIG)] == [rows[2].id]

    def test_only_series_merchants(self) -> None:
        rows = self._monthly([1549, 1549, 1799])
        assert price_increases(rows, set(), CONFIG) == []

    def test_double_charge_day_does_not_count_as_a_rise(self) -> None:
        rows = [*self._monthly([1549, 1549, 1549]), charge(3098, 60, merchant=5)]
        assert price_increases(rows, {5}, CONFIG) == []


def test_empty_input() -> None:
    assert detect_anomalies([], set()) == []


@pytest.mark.parametrize("seed", range(10))
def test_planted_anomalies_found_with_few_alerts(seed: int) -> None:
    """SPEC.md §6: every planted anomaly found; <= 5 alerts/month on typical data."""
    data = anomaly_dataset(seed)
    found = detect_anomalies(data.charges, data.series_merchants)
    assert data.planted <= {(a.transaction_id, a.kind) for a in found}
    assert len(found) / data.months <= 5
    assert len({(a.transaction_id, a.kind) for a in found}) == len(found)
