"""Streamlit entry point: page router. Pages live in app/views/ and stay thin.

The folder is deliberately not named `pages/`: Streamlit auto-discovers that name and
routes to its files directly, bypassing st.navigation (titles, URLs, page order).
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

PAGES = Path(__file__).parent / "views"

st.set_page_config(page_title="Spendsight", layout="wide")
navigation = st.navigation(
    [
        st.Page(PAGES / "home.py", title="Home", default=True),
        st.Page(PAGES / "overview.py", title="Overview", url_path="overview"),
        st.Page(PAGES / "transactions.py", title="Transactions", url_path="transactions"),
        st.Page(PAGES / "import_page.py", title="Import", url_path="import"),
    ]
)
navigation.run()
