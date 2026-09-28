"""Category classifier (SPEC.md §6), cascade step 4. Pure: labeled examples in, a model out.

Features: TF-IDF over character 3-5 grams of the merchant key, an amount bucket (with
sign), and the Chase category, one-hot. Model: logistic regression. Trained from labels
the user or a rule gave, never from its own output, so it can't reinforce its guesses.

Each merchant's rows share one unit of weight: the classifier's job is to generalize to
merchants the user hasn't labeled (a labeled merchant is already caught by its merchant
default, cascade step 3), so a merchant with 200 charges shouldn't drown out the rest.
For the same reason accuracy is measured on held-out *merchants*.

scikit-learn is imported lazily: below MIN_LABELS nothing is trained.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MIN_LABELS = 100  # below this the classifier is off (SPEC.md §6)
# Upper bounds (cents, absolute) of the amount buckets; beyond the last is its own bucket.
AMOUNT_BUCKETS = (500, 2000, 5000, 10000, 25000, 100000)


@dataclass(frozen=True)
class Example:
    key: str  # merchant normalized_key (or the raw description when there is none)
    amount_cents: int
    chase_category: str | None


@dataclass(frozen=True)
class LabeledExample:
    example: Example
    category_id: int


@dataclass(frozen=True)
class Prediction:
    category_id: int
    conf: float  # the model's probability for that category, rounded to 3 places


def amount_bucket(cents: int) -> str:
    sign = "in" if cents > 0 else "out"
    size = next((i for i, bound in enumerate(AMOUNT_BUCKETS) if abs(cents) < bound), None)
    return f"{sign}{len(AMOUNT_BUCKETS) if size is None else size}"


def _frame(examples: Sequence[Example]) -> Any:
    import pandas as pd

    return pd.DataFrame(
        {
            "key": [e.key for e in examples],
            "bucket": [amount_bucket(e.amount_cents) for e in examples],
            "chase": [(e.chase_category or "").casefold() or "none" for e in examples],
        }
    )


def _weights(labeled: Sequence[LabeledExample]) -> list[float]:
    """1 / (rows for that merchant), scaled to average 1 so regularization strength
    doesn't depend on how the weight is split."""
    per_key = Counter(item.example.key for item in labeled)
    raw = [1 / per_key[item.example.key] for item in labeled]
    scale = len(raw) / sum(raw)
    return [w * scale for w in raw]


class CategoryClassifier:
    def __init__(self, model: Any, labels: int) -> None:
        self._model = model
        self.labels = labels  # how many labeled examples it was trained on

    def predict(self, examples: Sequence[Example]) -> list[Prediction]:
        if not examples:
            return []
        probabilities = self._model.predict_proba(_frame(examples))
        classes = [int(c) for c in self._model.classes_]
        out = []
        for row in probabilities:
            best = int(row.argmax())
            out.append(Prediction(classes[best], round(float(row[best]), 3)))
        return out


def train(
    labeled: Sequence[LabeledExample], *, min_labels: int = MIN_LABELS
) -> CategoryClassifier | None:
    """None when there are too few labels or only one category to learn."""
    if len(labeled) < min_labels or len({item.category_id for item in labeled}) < 2:
        return None
    from sklearn.compose import ColumnTransformer
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder

    features = ColumnTransformer(
        [
            (
                "key",
                TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True),
                "key",
            ),
            ("onehot", OneHotEncoder(handle_unknown="ignore"), ["bucket", "chase"]),
        ]
    )
    model = Pipeline([("features", features), ("lr", LogisticRegression(C=5.0, max_iter=2000))])
    model.fit(
        _frame([item.example for item in labeled]),
        [item.category_id for item in labeled],
        lr__sample_weight=_weights(labeled),
    )
    return CategoryClassifier(model, len(labeled))


@dataclass(frozen=True)
class HoldoutReport:
    """Scores on merchants left out of training. `accuracy` is over every held-out row
    (the SPEC.md §6 target); the cascade only applies predictions at or above the
    threshold, so `confident_accuracy` and `coverage` describe what users actually see."""

    rows: int
    accuracy: float
    confident_accuracy: float | None  # None when nothing cleared the threshold
    coverage: float  # share of held-out rows at or above the threshold


def merchant_holdout(
    labeled: Sequence[LabeledExample], *, threshold: float, folds: int = 5
) -> HoldoutReport | None:
    """Grouped k-fold by merchant key. None when there aren't enough merchants to hold
    any out."""
    keys = sorted({item.example.key for item in labeled})
    if len(keys) < 2:
        return None
    fold_of = {key: i % min(folds, len(keys)) for i, key in enumerate(keys)}
    total = correct = confident = confident_correct = 0
    for fold in set(fold_of.values()):
        train_part = [item for item in labeled if fold_of[item.example.key] != fold]
        test_part = [item for item in labeled if fold_of[item.example.key] == fold]
        model = train(train_part, min_labels=0)
        if model is None:
            continue
        predictions = model.predict([item.example for item in test_part])
        for p, item in zip(predictions, test_part, strict=True):
            hit = p.category_id == item.category_id
            total += 1
            correct += hit
            if p.conf >= threshold:
                confident += 1
                confident_correct += hit
    if total == 0:
        return None
    return HoldoutReport(
        rows=total,
        accuracy=correct / total,
        confident_accuracy=confident_correct / confident if confident else None,
        coverage=confident / total,
    )
