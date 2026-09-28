"""Migration 008 rebuilds tables to drop foreign keys on edited reference columns."""

import duckdb
import pytest

from spendsight.db import repository
from spendsight.db.connection import migrate
from spendsight.pipeline import import_and_process
from tests.conftest import fixture_bytes

TABLES = [
    "categories",
    "merchants",
    "transactions",
    "category_rules",
    "chase_category_mappings",
    "recurring_series",
    "anomalies",
]


def _snapshot(conn: duckdb.DuckDBPyConnection) -> dict[str, list[tuple[object, ...]]]:
    snap = {}
    for table in TABLES:
        cursor = conn.execute(f"SELECT * FROM {table}")
        columns = [d[0] for d in cursor.description]
        rows = [dict(zip(columns, r, strict=True)) for r in cursor.fetchall()]
        snap[table] = sorted(tuple(sorted(r.items())) for r in rows)
    return snap


@pytest.fixture
def v7() -> duckdb.DuckDBPyConnection:
    """A database at version 7 with real-shaped data, including an anomaly row."""
    conn = duckdb.connect(":memory:")
    migrate(conn, up_to=7)
    import_and_process(conn, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    import_and_process(
        conn, fixture_bytes("chase_checking_jan.csv"), filename="k.csv", last4="0000"
    )
    conn.execute(
        "INSERT INTO anomalies (transaction_id, kind, score, explanation) "
        "SELECT id, 'amount_outlier', 3.5, 'synthetic' FROM transactions LIMIT 1"
    )
    conn.execute(
        "INSERT INTO recurring_series (merchant_id, cadence_days, typical_cents, status) "
        "SELECT id, 30, 1549, 'active' FROM merchants LIMIT 1"
    )
    return conn


def test_problem_reproduces_before_008(v7: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(duckdb.ConstraintException):
        v7.execute("UPDATE merchants SET default_category_id = 1, source = 'user'")


def test_008_preserves_every_row(v7: duckdb.DuckDBPyConnection) -> None:
    before = _snapshot(v7)
    assert migrate(v7)[0] == "008_mutable_reference_columns.sql"
    assert _snapshot(v7) == before
    assert repository.integrity_problems(v7) == []


def test_edits_work_after_008(v7: duckdb.DuckDBPyConnection) -> None:
    migrate(v7)
    v7.execute("UPDATE merchants SET default_category_id = 1, source = 'user'")
    # The anomaly still references a transaction; its category can be edited now.
    v7.execute(
        "UPDATE transactions SET category_id = 2, category_source = 'user', category_conf = 1.0 "
        "WHERE id IN (SELECT transaction_id FROM anomalies)"
    )
    v7.execute("UPDATE categories SET parent_id = 1 WHERE name = 'Uncategorized'")


def test_kept_foreign_keys_still_enforced(v7: duckdb.DuckDBPyConnection) -> None:
    migrate(v7)
    # Deleting a category used by a rule is still blocked.
    with pytest.raises(duckdb.ConstraintException):
        v7.execute(
            "DELETE FROM categories WHERE id = (SELECT category_id FROM category_rules LIMIT 1)"
        )
    # Anomalies must still point at a real transaction.
    with pytest.raises(duckdb.ConstraintException):
        v7.execute("INSERT INTO anomalies (transaction_id, kind) VALUES ('missing', 'duplicate')")


def test_sequences_continue_after_rebuild(v7: duckdb.DuckDBPyConnection) -> None:
    migrate(v7)
    (max_id,) = v7.execute("SELECT max(id) FROM categories").fetchone() or (0,)
    (new_id,) = v7.execute(
        "INSERT INTO categories (name) VALUES ('Synthetic New') RETURNING id"
    ).fetchone() or (0,)
    assert new_id > max_id


def test_integrity_problems_reports_dangling_reference(db: duckdb.DuckDBPyConnection) -> None:
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    db.execute(
        "UPDATE merchants SET default_category_id = 99999, source = 'user' "
        "WHERE normalized_key = 'SYNTH GROCER'"
    )
    assert repository.integrity_problems(db) == [
        "merchants.default_category_id: 1 row(s) point at a missing categories row"
    ]


def test_set_transaction_category_rejects_unknown_category(
    db: duckdb.DuckDBPyConnection,
) -> None:
    import_and_process(db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000")
    (txn_id,) = db.execute("SELECT id FROM transactions LIMIT 1").fetchone() or ("",)
    with pytest.raises(ValueError, match="unknown category"):
        repository.set_transaction_category(db, str(txn_id), 99999)
