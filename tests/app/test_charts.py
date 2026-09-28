from datetime import date

import pandas as pd

from spendsight.app.charts import fold_categories, monthly_spend_chart, ranked_bar_chart
from spendsight.app.theme import CATEGORY_SLOTS, OTHER, category_color

JAN, FEB = date(2026, 1, 1), date(2026, 2, 1)


def _monthly(rows: list[tuple[date, str, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["month", "parent_category", "spend_cents"])


def test_fold_keeps_slot_categories_and_sums_the_rest() -> None:
    folded = fold_categories(
        _monthly(
            [
                (JAN, "Health & Wellness", 1000),
                (JAN, "Uncategorized", 2500),
                (JAN, "Housing", 120000),
                (JAN, "Food & Dining", 5000),
                (FEB, "Education", 300),
            ]
        )
    )
    assert list(folded.itertuples(index=False, name=None)) == [
        (JAN, "Food & Dining", 5000),
        (JAN, "Housing", 120000),
        (JAN, OTHER, 3500),
        (FEB, OTHER, 300),
    ]


def test_fold_preserves_total_cents() -> None:
    monthly = _monthly([(JAN, name, 101) for name in ["Travel", "Gifts & Donations", "Other"]])
    assert int(fold_categories(monthly)["spend_cents"].sum()) == 303


def test_color_follows_category_not_rank() -> None:
    both = monthly_spend_chart(_monthly([(JAN, "Travel", 1), (JAN, "Housing", 1)]), "light")
    travel_only = monthly_spend_chart(_monthly([(JAN, "Travel", 1)]), "light")
    color = {t.name: t.marker.color for t in both.data}
    assert travel_only.data[0].marker.color == color["Travel"] == category_color("Travel", "light")


def test_at_most_eight_series() -> None:
    names = [*CATEGORY_SLOTS, "Health & Wellness", "Education", "Uncategorized"]
    fig = monthly_spend_chart(_monthly([(JAN, n, 100) for n in names]), "dark")
    assert len(fig.data) == 8
    assert fig.data[-1].name == OTHER


def test_hover_text_is_formatted_from_cents() -> None:
    fig = monthly_spend_chart(_monthly([(JAN, "Housing", 123456)]), "light")
    assert list(fig.data[0].customdata) == ["$1,234.56"]


def test_ranked_bar_labels() -> None:
    fig = ranked_bar_chart(["Rent", "Coffee"], [120000, 950], "light")
    assert list(fig.data[0].text) == ["$1,200.00", "$9.50"]


def test_theme_slots_are_distinct() -> None:
    for mode in ("light", "dark"):
        colors = [category_color(c, mode) for c in CATEGORY_SLOTS]  # type: ignore[arg-type]
        assert len(set(colors)) == len(CATEGORY_SLOTS)
        assert category_color("Uncategorized", mode) not in colors  # type: ignore[arg-type]
