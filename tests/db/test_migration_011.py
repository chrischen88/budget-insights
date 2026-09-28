"""Migration 011: ML output tables stop holding foreign keys; anomalies get an identity.

The bug it fixes: DuckDB rejects `UPDATE ... RETURNING` on a row another table references,
so with a recurring series on a merchant, "apply to merchant" and LLM naming failed; with
an anomaly on a transaction, recategorizing it failed.
"""

from datetime import date

import duckdb
import pytest

from spendsight.db import repository
from spendsight.db.connection import migrate
from spendsight.pipeline import import_and_process
from tests.synth import alerts_card_csv


@pytest.fixture
def v10() -> duckdb.DuckDBPyConnection:
    """Version 10 with a recurring series (cancelled by the user) and an anomaly."""
    conn = duckdb.connect(":memory:")
    migrate(conn, up_to=10)
    conn.execute("INSERT INTO accounts VALUES ('chase-card-0000', 'credit_card', 'Card', '0000')")
    conn.execute("INSERT INTO merchants (id, normalized_key) VALUES (1, 'SYNTH STREAMING')")
    conn.execute(
        "INSERT INTO transactions (id, account_id, txn_date, amount_cents, raw_description, "
        "merchant_id) VALUES ('t1', 'chase-card-0000', DATE '2025-11-03', -1799, "
        "'SYNTH STREAMING', 1)"
    )
    conn.execute(
        "INSERT INTO recurring_series (merchant_id, cadence_days, typical_cents, last_seen, "
        "next_expected, status, cancelled_on) VALUES (1, 30, -1549, DATE '2025-11-03', "
        "DATE '2025-12-03', 'active', DATE '2025-11-10')"
    )
    conn.execute(
        "INSERT INTO anomalies (transaction_id, kind, score, explanation, dismissed) "
        "VALUES ('t1', 'price_increase', 0.16, 'synthetic', TRUE)"
    )
    return conn


def test_problem_reproduces_before_011(v10: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(duckdb.ConstraintException):
        v10.execute("UPDATE merchants SET clean_name = 'x' WHERE id = 1 RETURNING id").fetchall()
    with pytest.raises(duckdb.ConstraintException):
        v10.execute("UPDATE transactions SET memo = 'x' WHERE id = 't1' RETURNING id").fetchall()


def test_011_preserves_rows_and_fixes_edits(v10: duckdb.DuckDBPyConnection) -> None:
    series_before = v10.execute("SELECT * FROM recurring_series").fetchall()
    anomalies_before = v10.execute("SELECT * FROM anomalies").fetchall()
    assert migrate(v10, up_to=11) == ["011_unreferenced_ml_tables.sql"]
    assert v10.execute("SELECT * FROM recurring_series").fetchall() == series_before
    assert v10.execute("SELECT * FROM anomalies").fetchall() == anomalies_before
    assert v10.execute("UPDATE merchants SET clean_name = 'x' WHERE id = 1 RETURNING id").fetchall()
    assert v10.execute("UPDATE transactions SET memo = 'x' WHERE id = 't1' RETURNING id").fetchall()
    assert repository.integrity_problems(v10) == []


def test_series_sequence_continues(v10: duckdb.DuckDBPyConnection) -> None:
    migrate(v10)
    v10.execute("INSERT INTO merchants (id, normalized_key) VALUES (2, 'OTHER')")
    (new_id,) = v10.execute(
        "INSERT INTO recurring_series (merchant_id) VALUES (2) RETURNING id"
    ).fetchone() or (0,)
    assert new_id == 2


def test_identities_are_unique(v10: duckdb.DuckDBPyConnection) -> None:
    migrate(v10)
    with pytest.raises(duckdb.ConstraintException):
        v10.execute("INSERT INTO anomalies (transaction_id, kind) VALUES ('t1', 'price_increase')")
    with pytest.raises(duckdb.ConstraintException):
        v10.execute("INSERT INTO recurring_series (merchant_id) VALUES (1)")


def test_integrity_check_reports_dangling_references(v10: duckdb.DuckDBPyConnection) -> None:
    migrate(v10)
    v10.execute("INSERT INTO anomalies (transaction_id, kind) VALUES ('missing', 'duplicate')")
    v10.execute("INSERT INTO recurring_series (merchant_id) VALUES (99)")
    assert repository.integrity_problems(v10) == [
        "recurring_series.merchant_id: 1 row(s) point at a missing merchants row",
        "anomalies.transaction_id: 1 row(s) point at a missing transactions row",
    ]


def test_repository_edits_with_series_and_anomalies(db: duckdb.DuckDBPyConnection) -> None:
    """The writers that broke, on a fully migrated database with ML output present."""
    import_and_process(db, alerts_card_csv(), filename="card.csv", last4="0000")
    [series] = [s for s in repository.list_recurring_series(db) if s.merchant == "SYNTH STREAMING"]
    merchant_id = db.execute(
        "SELECT merchant_id FROM recurring_series WHERE id = ?", [series.id]
    ).fetchone()
    assert merchant_id is not None
    category = repository.list_categories(db)[0].id
    # LLM naming first: it only writes merchants the user hasn't claimed.
    assert repository.save_llm_merchant_names(db, [(merchant_id[0], "Streaming Co", None, None)])
    repository.set_merchant_default(db, merchant_id[0], category)
    [alert] = repository.list_anomalies(db, date(2025, 11, 1), date(2025, 11, 30))
    assert repository.set_transaction_category(db, alert.transaction_id, category)
    assert repository.integrity_problems(db) == []
