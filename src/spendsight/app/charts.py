"""Plotly figures for the app. Pure: DataFrames of integer cents in, figures out.

Dollar values are divided by 100 only to place marks on the axis. Every number a user
reads (hover text, bar labels, tables) is formatted from cents by money.format_cents.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from spendsight.app.theme import (
    BACKGROUND,
    CATEGORY_SLOTS,
    GRID,
    OTHER,
    Mode,
    category_color,
    magnitude_color,
)
from spendsight.money import format_cents


def fold_categories(monthly: pd.DataFrame) -> pd.DataFrame:
    """Keep the fixed-slot categories; sum the rest into "Other" per month.

    Input/output columns: month, parent_category, spend_cents. Output rows are in stack
    order (slot order, then Other), so adjacent segments are the validated pairs.
    """
    if monthly.empty:
        return monthly.copy()
    folded = monthly.assign(
        parent_category=monthly["parent_category"].where(
            monthly["parent_category"].isin(CATEGORY_SLOTS), OTHER
        )
    )
    grouped = folded.groupby(["month", "parent_category"], as_index=False).agg(
        spend_cents=("spend_cents", "sum")
    )
    order = {name: i for i, name in enumerate((*CATEGORY_SLOTS, OTHER))}
    grouped["_order"] = grouped["parent_category"].map(order)
    return grouped.sort_values(["_order", "month"]).drop(columns="_order").reset_index(drop=True)


def _base_layout(fig: go.Figure, mode: Mode, *, height: int) -> None:
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel={"namelength": -1},
        bargap=0.35,
        barcornerradius=4,
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor=GRID[mode], gridwidth=1, zeroline=False)


def monthly_spend_chart(monthly: pd.DataFrame, mode: Mode) -> go.Figure:
    folded = fold_categories(monthly)
    fig = go.Figure()
    for name in dict.fromkeys(folded["parent_category"]):
        rows = folded[folded["parent_category"] == name]
        fig.add_bar(
            name=name,
            x=rows["month"],
            y=rows["spend_cents"] / 100,
            customdata=[format_cents(int(c)) for c in rows["spend_cents"]],
            marker={
                "color": category_color(str(name), mode),
                "line": {"color": BACKGROUND[mode], "width": 2},
            },
            hovertemplate="<b>%{fullData.name}</b><br>%{x|%b %Y}: %{customdata}<extra></extra>",
        )
    _base_layout(fig, mode, height=380)
    fig.update_layout(
        barmode="relative",  # stacks; net-refund months go below zero instead of hiding
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0, "traceorder": "normal"},
        margin={"t": 40},
    )
    fig.update_xaxes(dtick="M1", tickformat="%b %Y")
    fig.update_yaxes(tickprefix="$", tickformat=",.0f")
    return fig


def ranked_bar_chart(labels: list[str], cents: list[int], mode: Mode) -> go.Figure:
    """Horizontal bars, largest on top, one hue, value labels at the bar ends."""
    fig = go.Figure(
        go.Bar(
            orientation="h",
            y=labels,
            x=[c / 100 for c in cents],
            text=[format_cents(c) for c in cents],
            textposition="outside",
            cliponaxis=False,
            marker={"color": magnitude_color(mode)},
            hovertemplate="<b>%{y}</b>: %{text}<extra></extra>",
        )
    )
    _base_layout(fig, mode, height=max(160, 48 + 30 * len(labels)))
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)")
    fig.update_xaxes(showticklabels=False, showgrid=False, zeroline=False)
    fig.update_layout(margin={"r": 80})
    return fig
