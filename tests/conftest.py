from collections.abc import Iterator

import duckdb
import pytest

from spendsight.db.connection import connect


@pytest.fixture
def db() -> Iterator[duckdb.DuckDBPyConnection]:
    """A fresh, fully migrated in-memory database."""
    conn = connect(":memory:")
    yield conn
    conn.close()
