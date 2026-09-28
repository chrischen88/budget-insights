"""DuckDB connections and the numbered-migration runner."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import duckdb

_MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def load_migrations() -> list[Migration]:
    """Read `db/migrations/NNN_name.sql` files in version order."""
    root = resources.files("spendsight.db") / "migrations"
    migrations = []
    for entry in root.iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            migrations.append(Migration(int(match.group(1)), entry.name, entry.read_text()))
    migrations.sort(key=lambda m: m.version)
    versions = [m.version for m in migrations]
    if len(set(versions)) != len(versions):
        raise RuntimeError(f"duplicate migration versions: {versions}")
    return migrations


def applied_versions(conn: duckdb.DuckDBPyConnection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row[0]) for row in rows}


def migrate(conn: duckdb.DuckDBPyConnection, *, up_to: int | None = None) -> list[str]:
    """Apply pending migrations, each in its own transaction. Returns names applied.

    `up_to` stops after that version (for testing a migration against older data).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TIMESTAMP NOT NULL DEFAULT current_timestamp
        )
        """
    )
    done = applied_versions(conn)
    applied = []
    for migration in load_migrations():
        if migration.version in done:
            continue
        if up_to is not None and migration.version > up_to:
            break
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                [migration.version, migration.name],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(migration.name)
    return applied


def connect(db_path: Path | str, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the database. Writable connections are migrated to the latest schema."""
    if read_only:
        return duckdb.connect(str(db_path), read_only=True)
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    migrate(conn)
    return conn
