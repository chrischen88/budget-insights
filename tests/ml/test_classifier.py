"""Category classifier: gating, features, and held-out accuracy on synthetic labels."""

import pytest

from spendsight.ml.classifier import (
    MIN_LABELS,
    Example,
    LabeledExample,
    amount_bucket,
    merchant_holdout,
    train,
)
from tests.synth import classifier_dataset

GROCERIES, RESTAURANTS = 1, 2


def _labels(n: int) -> list[LabeledExample]:
    """n labels over two obvious categories, a few rows per merchant."""
    out = []
    for i in range(n):
        if i % 2:
            out.append(LabeledExample(Example(f"BRAND{i // 6} PIZZA", -3000, None), RESTAURANTS))
        else:
            out.append(LabeledExample(Example(f"BRAND{i // 6} MARKET", -8000, None), GROCERIES))
    return out


@pytest.mark.parametrize(
    ("cents", "bucket"),
    [
        (-499, "out0"),
        (-500, "out1"),
        (-1999, "out1"),
        (-99999, "out5"),
        (-100000, "out6"),
        (250000, "in6"),
        (1500, "in1"),
    ],
)
def test_amount_bucket(cents: int, bucket: str) -> None:
    assert amount_bucket(cents) == bucket


def test_off_below_minimum_labels() -> None:
    assert train(_labels(MIN_LABELS - 1)) is None
    assert train(_labels(MIN_LABELS)) is not None


def test_off_with_a_single_category() -> None:
    only = [LabeledExample(Example(f"M{i}", -1000, None), GROCERIES) for i in range(200)]
    assert train(only) is None


def test_generalizes_to_unseen_merchants() -> None:
    model = train(_labels(120))
    assert model is not None
    assert model.labels == 120
    pizza, market = model.predict(
        [Example("NEWCO PIZZA", -2500, None), Example("OTHERCO MARKET", -9000, None)]
    )
    assert pizza.category_id == RESTAURANTS
    assert pizza.conf > 0.5
    assert market.category_id == GROCERIES
    assert model.predict([]) == []


def test_confidence_is_rounded() -> None:
    model = train(_labels(120))
    assert model is not None
    [p] = model.predict([Example("NEWCO PIZZA", -2500, None)])
    assert p.conf == round(p.conf, 3)
    assert 0 < p.conf <= 1


def test_one_busy_merchant_does_not_drown_the_rest() -> None:
    """300 rows of one mislabeled-looking merchant vs 100 merchants with one row each:
    per-merchant weighting keeps the many merchants' pattern."""
    busy = [LabeledExample(Example("BIGCO PIZZA", -3000, None), GROCERIES)] * 300
    many = [LabeledExample(Example(f"CO{i} PIZZA", -3000, None), RESTAURANTS) for i in range(100)]
    other = [LabeledExample(Example(f"CO{i} MARKET", -8000, None), GROCERIES) for i in range(20)]
    model = train([*busy, *many, *other])
    assert model is not None
    [p] = model.predict([Example("NEWCO PIZZA", -3000, None)])
    assert p.category_id == RESTAURANTS


def test_deterministic() -> None:
    data = classifier_dataset(0)
    a, b = train(data), train(data)
    assert a is not None
    assert b is not None
    examples = [item.example for item in data[:50]]
    assert a.predict(examples) == b.predict(examples)


def test_holdout_meets_targets_at_300_labels() -> None:
    """SPEC.md §6: >= 85% held-out accuracy once >= 300 labels exist, held out by merchant
    (a labeled merchant is caught by its merchant default, so unseen merchants are what
    matter). Averaged over seeds: with ~55 merchants one seed can hold out the only "VET"
    merchant. What the cascade applies (conf >= 0.75) must be right on every seed."""
    reports = []
    for seed in range(10):
        data = classifier_dataset(seed, merchants=55)
        assert len(data) >= 300
        report = merchant_holdout(data, threshold=0.75)
        assert report is not None
        assert report.confident_accuracy is not None
        assert report.confident_accuracy >= 0.95, seed
        assert report.coverage >= 0.6, seed
        reports.append(report)
    assert sum(r.accuracy for r in reports) / len(reports) >= 0.85


def test_holdout_needs_two_merchants() -> None:
    one = [LabeledExample(Example("SOLO", -1000, None), GROCERIES)] * 5
    assert merchant_holdout(one, threshold=0.75) is None
