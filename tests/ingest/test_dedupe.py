import hashlib
from datetime import date

from spendsight.ingest.dedupe import assign_ids, to_frame, transaction_id
from spendsight.ingest.models import ParsedTransaction

COFFEE = ParsedTransaction(date(2026, 1, 3), None, -475, "SYNTH COFFEE CO")
GROCER = ParsedTransaction(date(2026, 1, 5), None, -8219, "SYNTH GROCER")


def test_ids_are_deterministic() -> None:
    assert assign_ids("acct", [COFFEE, GROCER]) == assign_ids("acct", [COFFEE, GROCER])


def test_identical_same_day_rows_get_distinct_ids() -> None:
    (id_a, _), (id_b, _) = assign_ids("acct", [COFFEE, COFFEE])
    assert id_a != id_b
    assert id_a == transaction_id("acct", COFFEE, 0)
    assert id_b == transaction_id("acct", COFFEE, 1)


def test_occurrence_index_ignores_other_rows() -> None:
    # A row's ID depends only on earlier identical rows, not on unrelated rows or order.
    ids_alone = dict(assign_ids("acct", [COFFEE]))
    ids_mixed = dict(assign_ids("acct", [GROCER, COFFEE]))
    assert set(ids_alone) <= set(ids_mixed)


def test_account_is_part_of_id() -> None:
    assert transaction_id("a", COFFEE, 0) != transaction_id("b", COFFEE, 0)


def test_known_hash_value() -> None:
    # Pins the §5.2 hash inputs. If this changes, every stored ID changes: that's a migration.
    expected = hashlib.sha256(b"chase-card-0000|2026-01-03|-475|SYNTH COFFEE CO|0").hexdigest()
    assert transaction_id("chase-card-0000", COFFEE, 0) == expected


def test_to_frame_columns() -> None:
    frame = to_frame(assign_ids("acct", [COFFEE, GROCER]))
    assert list(frame.columns) == [
        "id",
        "txn_date",
        "post_date",
        "amount_cents",
        "raw_description",
        "chase_category",
        "chase_type",
        "memo",
    ]
    assert frame["amount_cents"].dtype == "int64"
