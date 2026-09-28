"""Recurring (SPEC.md §9): subscriptions with cadence, amount, next date, annualized cost;
dismiss / mark cancelled."""

from __future__ import annotations

import streamlit as st

from spendsight.app.formatting import recurring_table
from spendsight.app.state import get_connection, local_today
from spendsight.db import repository
from spendsight.insights import recurring
from spendsight.ml.anomaly_runner import run_anomaly_detection
from spendsight.money import format_cents

conn = get_connection()
st.title("Recurring")

if repository.latest_transaction_date(conn) is None:
    st.info("No transactions yet. Import a Chase CSV on the **Import** page to get started.")
    st.stop()

if message := st.session_state.pop("recurring-message", None):
    st.success(message)

charges = recurring.list_recurring(conn, today=local_today())
current = [c for c in charges if c.is_current]
set_aside = [c for c in charges if not c.is_current]

if not charges:
    st.caption(
        "No recurring charges found yet. A merchant needs at least three charges on a "
        "steady schedule (weekly to annual) at a similar amount."
    )
    st.stop()

active = [c for c in current if c.status == "active"]
col_count, col_annual, col_lapsed = st.columns(3)
col_count.metric("Active subscriptions", len(active))
col_annual.metric(
    "Annualized cost", format_cents(recurring.annualized_total(charges), signed=False)
)
lapsed = len(current) - len(active)
if lapsed:
    col_lapsed.metric("Lapsed", lapsed, help="No charge since the expected date.")

if current:
    st.caption(
        "Select a row to dismiss it or mark it cancelled. Lapsed charges aren't counted "
        "in the annualized cost."
    )
    selection = st.dataframe(
        recurring_table(current),
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="recurring-table",
    )
    if selection.selection.rows:
        picked = current[selection.selection.rows[0]]
        st.subheader(picked.merchant)
        dismiss_col, cancel_col, _ = st.columns([1, 1, 3])
        if dismiss_col.button("Not a subscription", key=f"dismiss-{picked.series_id}"):
            repository.dismiss_recurring(conn, picked.series_id)
            run_anomaly_detection(conn)  # price increases skip dismissed series
            st.session_state["recurring-message"] = f"Dismissed {picked.merchant}."
            st.rerun()
        if cancel_col.button("Mark cancelled", type="primary", key=f"cancel-{picked.series_id}"):
            repository.cancel_recurring(conn, picked.series_id, local_today())
            st.session_state["recurring-message"] = f"Marked {picked.merchant} as cancelled."
            st.rerun()

if set_aside:
    with st.expander(f"Dismissed and cancelled ({len(set_aside)})"):
        restore = st.dataframe(
            recurring_table(set_aside),
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="recurring-set-aside",
        )
        if restore.selection.rows:
            picked = set_aside[restore.selection.rows[0]]
            if st.button(f"Restore {picked.merchant}", key=f"restore-{picked.series_id}"):
                repository.restore_recurring(conn, picked.series_id)
                run_anomaly_detection(conn)
                st.session_state["recurring-message"] = f"Restored {picked.merchant}."
                st.rerun()
