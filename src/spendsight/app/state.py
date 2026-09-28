"""Shared app resources: settings and the database connection."""

from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb
import streamlit as st

from spendsight.config import Settings, load_settings
from spendsight.db.connection import connect


def get_settings() -> Settings:
    return load_settings()


@st.cache_resource
def _database(db_path: str) -> duckdb.DuckDBPyConnection:
    """One migrated connection per database file for the whole server process."""
    return connect(db_path)


def get_connection() -> duckdb.DuckDBPyConnection:
    """A cursor for this script run. Streamlit runs sessions on separate threads and a
    DuckDB connection must not be shared across threads; cursors are cheap and safe."""
    return _database(str(get_settings().db_path)).cursor()


def local_today() -> date:
    """Today's date in the machine's local timezone: statement dates are local calendar
    dates. Logic takes `today` as a parameter; only pages call this."""
    return datetime.now(UTC).astimezone().date()
