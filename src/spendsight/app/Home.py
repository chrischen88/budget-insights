"""Streamlit entry point. Pages stay thin: they call insights/repository code and render."""

from __future__ import annotations

import streamlit as st

from spendsight.config import load_settings
from spendsight.db.connection import connect

st.set_page_config(page_title="Spendsight", layout="wide")

settings = load_settings()
conn = connect(settings.db_path)

st.title("Spendsight")
st.caption("Local-first spending insights for Chase exports.")

if settings.local_only:
    st.info("Local-only mode: LLM features are disabled.")
elif not settings.llm_enabled:
    st.info(f"LLM features are off (no API key or model configured for {settings.llm_provider}).")

st.write("Import and Overview pages arrive in Phase 1. Database is initialized and migrated.")
