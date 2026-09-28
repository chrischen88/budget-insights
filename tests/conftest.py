from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from spendsight.db.connection import connect


@pytest.fixture
def db() -> Iterator[duckdb.DuckDBPyConnection]:
    """A fresh, fully migrated in-memory database."""
    conn = connect(":memory:")
    yield conn
    conn.close()


FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()
