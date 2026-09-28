"""Overview metrics against the fixtures, with every expected value computed by hand.

After importing both January fixtures, the $500 card payment pairs with its checking
side and is excluded. Remaining rows (cents), by flow:

  card spend:     streaming 1549, late fee 1500, online store 12999 - 2999 return,
                  grocer 8219, coffee 475 x2                          = 22218
  checking spend: shop 1000, ATM 6000, service fee 1200, check 15000,
                  market 2340, rent 120000                            = 145540
  income:         payroll 250000 (Paycheck, via seed rule)
"""

from datetime import date

import duckdb
import pytest

from spendsight.insights import metrics
from spendsight.insights.metrics import Kpis, SpendFilter
from spendsight.pipeline import import_and_process
from tests.conftest import fixture_bytes

JAN = SpendFilter(date(2026, 1, 1), date(2026, 1, 31))


@pytest.fixture
def loaded(db: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    import_and_process(db, fixture_bytes("chase_checking_jan.csv"), filename="k.csv", last4="0000")
    return db


def test_kpis(loaded: duckdb.DuckDBPyConnection) -> None:
    k = metrics.kpis(loaded, JAN)
    assert k == Kpis(spent_cents=22218 + 145540, income_cents=250000)
    assert k.net_cents == 82242


def test_net_equals_sum_of_amounts(loaded: duckdb.DuckDBPyConnection) -> None:
    row = loaded.execute("SELECT sum(amount_cents) FROM v_spend").fetchone()
    assert row == (metrics.kpis(loaded, JAN).net_cents,)


def test_refund_nets_against_spend_not_income(loaded: duckdb.DuckDBPyConnection) -> None:
    by_cat = dict(metrics.spend_by_category(loaded, JAN).itertuples(index=False))
    assert by_cat["Shopping"] == 12999 - 2999


def test_spend_by_category(loaded: duckdb.DuckDBPyConnection) -> None:
    frame = metrics.spend_by_category(loaded, JAN)
    assert list(frame.itertuples(index=False, name=None)) == [
        ("Uncategorized", 1000 + 15000 + 2340 + 120000),
        ("Shopping", 10000),
        ("Food & Dining", 8219 + 475 + 475),
        ("Cash & ATM", 6000),  # seed rule
        ("Fees & Charges", 1500 + 1200),  # card late fee + checking service fee (seed rule)
        ("Entertainment", 1549),
    ]


def test_monthly_spend_by_category(loaded: duckdb.DuckDBPyConnection) -> None:
    frame = metrics.monthly_spend_by_category(loaded, JAN)
    assert set(frame["month"]) == {date(2026, 1, 1)}
    assert int(frame["spend_cents"].sum()) == 167758


def test_top_merchants(loaded: duckdb.DuckDBPyConnection) -> None:
    frame = metrics.top_merchants(loaded, JAN, limit=3)
    assert list(frame.itertuples(index=False, name=None)) == [
        ("SYNTH RENT CO WEB PMTS", 120000, 1),
        ("CHECK", 15000, 1),  # merchant shown by its cleaned key
        ("SYNTH ONLINE STORE", 10000, 2),  # sale net of its return
    ]
    # Income never appears as a merchant.
    all_merchants = set(metrics.top_merchants(loaded, JAN, limit=100)["merchant"])
    assert "SYNTH EMPLOYER PAYROLL" not in all_merchants


def test_include_transfers(loaded: duckdb.DuckDBPyConnection) -> None:
    k = metrics.kpis(loaded, SpendFilter(JAN.start, JAN.end, include_transfers=True))
    # Checking -500 counts as spend, the uncategorized card +500 as income; net unchanged.
    assert k == Kpis(spent_cents=167758 + 50000, income_cents=250000 + 50000)
    assert k.net_cents == 82242


def test_account_filter(loaded: duckdb.DuckDBPyConnection) -> None:
    card_only = SpendFilter(JAN.start, JAN.end, account_ids=("chase-card-0000",))
    assert metrics.kpis(loaded, card_only) == Kpis(spent_cents=22218, income_cents=0)
    nothing = SpendFilter(JAN.start, JAN.end, account_ids=())
    assert metrics.kpis(loaded, nothing) == Kpis(0, 0)


def test_date_filter_is_inclusive(loaded: duckdb.DuckDBPyConnection) -> None:
    # Through 01/10: card return, sale, grocer, 2 coffees; checking check, market, rent, payroll.
    first_ten = SpendFilter(date(2026, 1, 1), date(2026, 1, 10))
    assert metrics.kpis(loaded, first_ten) == Kpis(
        spent_cents=(12999 - 2999 + 8219 + 950) + (15000 + 2340 + 120000),
        income_cents=250000,
    )


def test_empty_prior_period(loaded: duckdb.DuckDBPyConnection) -> None:
    assert metrics.kpis(loaded, JAN.prior()) == Kpis(0, 0)
    assert metrics.spend_by_category(loaded, JAN.prior()).empty


def test_date_bounds(db: duckdb.DuckDBPyConnection, loaded: duckdb.DuckDBPyConnection) -> None:
    assert metrics.date_bounds(loaded) == (date(2026, 1, 2), date(2026, 1, 21))


def test_date_bounds_empty(db: duckdb.DuckDBPyConnection) -> None:
    assert metrics.date_bounds(db) is None


@pytest.mark.parametrize(
    ("start", "end", "prior_start", "prior_end"),
    [
        (date(2026, 1, 1), date(2026, 1, 31), date(2025, 12, 1), date(2025, 12, 31)),
        (date(2026, 3, 1), date(2026, 3, 1), date(2026, 2, 28), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 31), date(2026, 1, 29), date(2026, 2, 28)),
    ],
)
def test_prior_period(start: date, end: date, prior_start: date, prior_end: date) -> None:
    prior = SpendFilter(start, end).prior()
    assert (prior.start, prior.end) == (prior_start, prior_end)
    assert prior.days == SpendFilter(start, end).days


def test_filter_rejects_inverted_range() -> None:
    with pytest.raises(ValueError, match="end"):
        SpendFilter(date(2026, 2, 1), date(2026, 1, 1))


def test_has_transactions(loaded: duckdb.DuckDBPyConnection) -> None:
    assert metrics.has_transactions(loaded, JAN)
    assert not metrics.has_transactions(loaded, JAN.prior())
