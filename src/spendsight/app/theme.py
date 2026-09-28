"""The one place chart colors come from.

Categorical hues are the dataviz reference palette in its validated slot order (checked
with the palette validator in both modes: adjacent CVD ΔE >= 8.4, normal-vision >= 19.3).
Each slot belongs to a fixed category, so a category keeps its color regardless of rank
or filters. Everything else folds into a neutral "Other" rather than a generated hue.
Three light-mode slots sit under 3:1 contrast, so every chart also offers a table view.
"""

from __future__ import annotations

from typing import Literal

Mode = Literal["light", "dark"]

OTHER = "Other"

# Slot order matters: it's what keeps neighbours in a stack distinguishable.
CATEGORY_SLOTS: tuple[str, ...] = (
    "Food & Dining",
    "Housing",
    "Transportation",
    "Shopping",
    "Bills & Utilities",
    "Entertainment",
    "Travel",
)

_SERIES: dict[Mode, tuple[str, ...]] = {
    "light": ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"),
    "dark": ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"),
}
_OTHER_COLOR: dict[Mode, str] = {"light": "#898781", "dark": "#898781"}
# Matches Streamlit's page background so the 2px gaps between stacked segments read as gaps.
BACKGROUND: dict[Mode, str] = {"light": "#ffffff", "dark": "#0e1117"}
GRID: dict[Mode, str] = {"light": "#e1e0d9", "dark": "#2c2c2a"}


def category_color(category: str, mode: Mode) -> str:
    if category in CATEGORY_SLOTS:
        return _SERIES[mode][CATEGORY_SLOTS.index(category)]
    return _OTHER_COLOR[mode]


def magnitude_color(mode: Mode) -> str:
    """Single hue for magnitude-only charts (rankings), where color carries no identity."""
    return _SERIES[mode][0]
