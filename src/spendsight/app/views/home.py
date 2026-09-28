"""Landing page."""

from __future__ import annotations

import streamlit as st

from spendsight.app.state import get_connection, get_settings
from spendsight.db import repository

settings = get_settings()
conn = get_connection()

st.title("Spendsight")
st.caption("Local-first spending insights for Chase exports.")

if settings.local_only:
    st.info("Local-only mode: LLM features are disabled.")
elif not settings.llm_enabled:
    st.info(f"LLM features are off (no API key or model configured for {settings.llm_provider}).")

if not repository.list_accounts(conn):
    st.write("No data yet. Start on the **Import** page with a Chase CSV export.")
