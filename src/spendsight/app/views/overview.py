"""Overview (SPEC.md §9): filters, KPIs, monthly spend by category, categories, merchants."""

from __future__ import annotations

from datetime import date

import streamlit as st

from spendsight.app.charts import monthly_spend_chart, ranked_bar_chart
from spendsight.app.state import get_connection
from spendsight.app.theme import Mode
from spendsight.db import repository
from spendsight.insights import metrics
from spendsight.money import format_cents

conn = get_connection()
mode: Mode = "dark" if st.context.theme.type == "dark" else "light"

st.title("Overview")

bounds = metrics.date_bounds(conn)
accounts = repository.list_accounts(conn)
if bounds is None or not accounts:
    st.info("No transactions yet. Import a Chase CSV on the **Import** page to get started.")
    st.stop()

# Filters: one row above the charts.
col_dates, col_accounts, col_transfers = st.columns([2, 2.6, 1.4], vertical_alignment="bottom")
picked = col_dates.date_input(
    "Date range", value=bounds, min_value=None, max_value=None, key="overview-dates"
)
names = {a.id: a.display_name for a in accounts}
account_ids = col_accounts.multiselect(
    "Accounts", options=list(names), default=list(names), format_func=names.__getitem__
)
include_transfers = col_transfers.toggle("Show transfers", value=False)

if not isinstance(picked, tuple) or len(picked) != 2:
    st.caption("Pick an end date to finish the range.")
    st.stop()
start, end = picked
if not isinstance(start, date) or not isinstance(end, date):
    st.stop()

f = metrics.SpendFilter(
    start=start,
    end=end,
    account_ids=tuple(account_ids),
    include_transfers=include_transfers,
)
current = metrics.kpis(conn, f)
# Only compare when the previous period has data; otherwise every delta is just the total.
prior = metrics.kpis(conn, f.prior()) if metrics.has_transactions(conn, f.prior()) else None


def _delta(now: int, before: int | None) -> str | None:
    return None if before is None else format_cents(now - before)


st.caption(
    f"Compared with the previous {f.days} days."
    if prior
    else f"No transactions in the previous {f.days} days to compare with."
)
k1, k2, k3 = st.columns(3)
k1.metric(
    "Spent",
    format_cents(current.spent_cents),
    delta=_delta(current.spent_cents, prior.spent_cents if prior else None),
    delta_color="inverse",
)
k2.metric(
    "Income",
    format_cents(current.income_cents),
    delta=_delta(current.income_cents, prior.income_cents if prior else None),
)
k3.metric(
    "Net",
    format_cents(current.net_cents),
    delta=_delta(current.net_cents, prior.net_cents if prior else None),
)

st.subheader("Monthly spending by category")
monthly = metrics.monthly_spend_by_category(conn, f)
if monthly.empty:
    st.caption("No spending in this range.")
else:
    st.plotly_chart(monthly_spend_chart(monthly, mode), key="monthly")
    with st.expander("Table"):
        table = monthly.assign(spend=monthly["spend_cents"].map(format_cents))
        st.dataframe(
            table[["month", "parent_category", "spend"]].rename(
                columns={"month": "Month", "parent_category": "Category", "spend": "Spent"}
            ),
            hide_index=True,
        )

left, right = st.columns(2)
with left:
    st.subheader("By category")
    by_category = metrics.spend_by_category(conn, f)
    if by_category.empty:
        st.caption("No spending in this range.")
    else:
        st.plotly_chart(
            ranked_bar_chart(
                list(by_category["parent_category"]),
                [int(c) for c in by_category["spend_cents"]],
                mode,
            ),
            key="by-category",
        )
        with st.expander("Table"):
            st.dataframe(
                by_category.assign(spend=by_category["spend_cents"].map(format_cents))[
                    ["parent_category", "spend"]
                ].rename(columns={"parent_category": "Category", "spend": "Spent"}),
                hide_index=True,
            )
with right:
    st.subheader("Top merchants")
    merchants = metrics.top_merchants(conn, f, limit=10)
    if merchants.empty:
        st.caption("No spending in this range.")
    else:
        st.plotly_chart(
            ranked_bar_chart(
                list(merchants["merchant"]), [int(c) for c in merchants["spend_cents"]], mode
            ),
            key="merchants",
        )
        with st.expander("Table"):
            st.dataframe(
                merchants.assign(spend=merchants["spend_cents"].map(format_cents))[
                    ["merchant", "spend", "transactions"]
                ].rename(
                    columns={
                        "merchant": "Merchant",
                        "spend": "Spent",
                        "transactions": "Transactions",
                    }
                ),
                hide_index=True,
            )
