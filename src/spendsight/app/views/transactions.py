"""Transactions (SPEC.md §9): search, filter, and recategorize with "apply to merchant"."""

from __future__ import annotations

from datetime import date

import streamlit as st

from spendsight.app.formatting import (
    category_label,
    recategorize_message,
    source_badge,
    transactions_table,
)
from spendsight.app.state import get_connection
from spendsight.db import repository
from spendsight.enrich.recategorize import recategorize, reset_to_automatic
from spendsight.insights import metrics
from spendsight.money import format_cents

ALL, UNCATEGORIZED = "All categories", "Uncategorized"

conn = get_connection()
st.title("Transactions")

bounds = metrics.date_bounds(conn)
accounts = repository.list_accounts(conn)
if bounds is None or not accounts:
    st.info("No transactions yet. Import a Chase CSV on the **Import** page to get started.")
    st.stop()

if message := st.session_state.pop("recategorize-message", None):
    st.success(message)

categories = repository.list_categories(conn)
by_label = {c.label: c.id for c in categories}

text = st.text_input("Search", placeholder="Merchant, description or memo", key="txn-search")
col_dates, col_accounts, col_category, col_transfers = st.columns(
    [2, 2.4, 2, 1.4], vertical_alignment="bottom"
)
picked = col_dates.date_input("Date range", value=bounds, key="txn-dates")
names = {a.id: a.display_name for a in accounts}
account_ids = col_accounts.multiselect(
    "Accounts", options=list(names), default=list(names), format_func=names.__getitem__
)
category_choice = col_category.selectbox(
    "Category", [ALL, UNCATEGORIZED, *by_label], key="txn-category"
)
include_transfers = col_transfers.toggle("Show transfers", value=False, key="txn-transfers")

if not isinstance(picked, tuple) or len(picked) != 2:
    st.caption("Pick an end date to finish the range.")
    st.stop()
start, end = picked
if not isinstance(start, date) or not isinstance(end, date):
    st.stop()

results, total = repository.search_transactions(
    conn,
    repository.TransactionQuery(
        start=start,
        end=end,
        account_ids=tuple(account_ids),
        text=text,
        category_id=by_label.get(category_choice),
        uncategorized_only=category_choice == UNCATEGORIZED,
        include_transfers=include_transfers,
    ),
)
if results.empty:
    st.caption("No transactions match these filters.")
    st.stop()

shown = f"Showing {len(results)} of {total}" if total > len(results) else f"{total}"
st.caption(f"{shown} transactions. Select a row to change its category.")
selection = st.dataframe(
    transactions_table(results),
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    key="txn-table",
)
if not selection.selection.rows:
    st.stop()

row = results.iloc[selection.selection.rows[0]]
txn_id = str(row["id"])
current = category_label(row["category"], row["parent_category"])
st.divider()
st.subheader(str(row["merchant"]))
st.caption(
    f"{row['date']:%b %d, %Y} · {row['account']} · {format_cents(int(row['amount_cents']))} · "
    f"{row['raw_description']}"
)
badge = source_badge(
    None if row.isna()["category_source"] else str(row["category_source"]),
    None if row.isna()["category_conf"] else float(row["category_conf"]),
    bool(row["category_locked"]),
)
st.write(f"Current category: **{current}**" + (f" ({badge})" if badge else ""))

labels = list(by_label)
new_label = st.selectbox(
    "New category",
    labels,
    index=labels.index(current) if current in by_label else None,
    placeholder="Choose a category",
    key=f"new-category-{txn_id}",
)
has_merchant = not row.isna()["merchant_id"]
apply_to_merchant = has_merchant and st.checkbox(
    f"Also apply to all {row['merchant']} transactions",
    key=f"apply-merchant-{txn_id}",
    help="Sets this merchant's default category. Rules still take precedence.",
)
save_col, reset_col = st.columns([1, 3])
if save_col.button("Save", type="primary", disabled=new_label is None, key=f"save-{txn_id}"):
    assert new_label is not None
    result = recategorize(
        conn, txn_id, by_label[new_label], apply_to_merchant=bool(apply_to_merchant)
    )
    st.session_state["recategorize-message"] = recategorize_message(
        category=new_label,
        merchant=str(row["merchant"]),
        applied_to_merchant=result.applied_to_merchant,
        merchant_rows=result.merchant_rows,
        following=result.following,
        held_by_override=result.held_by_override,
        held_by_rule=result.held_by_rule,
    )
    st.rerun()
if bool(row["category_locked"]) and reset_col.button("Reset to automatic", key=f"reset-{txn_id}"):
    reset_to_automatic(conn, txn_id)
    st.session_state["recategorize-message"] = "Reset. The category is automatic again."
    st.rerun()
