"""Alerts (SPEC.md §9): anomalies with plain-English explanations; dismiss."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from spendsight.app.formatting import alerts_table
from spendsight.app.state import get_connection
from spendsight.db import repository
from spendsight.insights import metrics

DEFAULT_DAYS = 90

conn = get_connection()
st.title("Alerts")

bounds = metrics.date_bounds(conn)
if bounds is None:
    st.info("No transactions yet. Import a Chase CSV on the **Import** page to get started.")
    st.stop()

if message := st.session_state.pop("alerts-message", None):
    st.success(message)

first, last = bounds
default = (max(first, last - timedelta(days=DEFAULT_DAYS - 1)), last)
col_dates, col_dismissed = st.columns([2, 1.4], vertical_alignment="bottom")
picked = col_dates.date_input("Date range", value=default, key="alerts-dates")
show_dismissed = col_dismissed.toggle("Show dismissed", value=False, key="alerts-dismissed")

if not isinstance(picked, tuple) or len(picked) != 2:
    st.caption("Pick an end date to finish the range.")
    st.stop()
start, end = picked
if not isinstance(start, date) or not isinstance(end, date):
    st.stop()

alerts = repository.list_anomalies(conn, start, end, include_dismissed=show_dismissed)
if not alerts:
    st.caption("No alerts in this period.")
    st.stop()

open_count = sum(not a.dismissed for a in alerts)
st.caption(
    f"{open_count} open alert{'s' if open_count != 1 else ''}. "
    "Select a row to dismiss it once you've checked it."
)
selection = st.dataframe(
    alerts_table(alerts, show_status=show_dismissed),
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    key="alerts-table",
)
if not selection.selection.rows:
    st.stop()

alert = alerts[selection.selection.rows[0]]
key = f"{alert.transaction_id}-{alert.kind}"
if not alert.dismissed and st.button("Dismiss", type="primary", key=f"dismiss-{key}"):
    repository.set_anomaly_dismissed(conn, alert.transaction_id, alert.kind, True)
    st.session_state["alerts-message"] = f"Dismissed the {alert.merchant} alert."
    st.rerun()
if alert.dismissed and st.button("Restore", key=f"restore-{key}"):
    repository.set_anomaly_dismissed(conn, alert.transaction_id, alert.kind, False)
    st.session_state["alerts-message"] = f"Restored the {alert.merchant} alert."
    st.rerun()
