"""Merchant naming inside the import pipeline, with a mocked provider (no network)."""

import json

import duckdb
import pytest

from spendsight.enrich.merchant_naming import is_p2p, run_merchant_naming
from spendsight.llm.client import LLMClient
from spendsight.llm.redact import Redactor
from spendsight.pipeline import import_and_process
from tests.conftest import fixture_bytes
from tests.llm.fakes import PROVIDERS, FakeSDK, default_responder, make_adapter

P2P_CHECKING = (
    b"Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    b"DEBIT,01/22/2026,Zelle payment to JANE SAMPLE 12345678,-40.00,QUICKPAY_DEBIT,0.00,,\n"
    b"DEBIT,01/23/2026,VENMO PAYMENT 1000000000 WEB ID: 0000000000,-12.00,ACH_DEBIT,0.00,,\n"
)


def _client(db: duckdb.DuckDBPyConnection, provider: str) -> tuple[LLMClient, FakeSDK]:
    adapter, sdk = make_adapter(provider)
    return LLMClient(db, adapter, "m1", Redactor()), sdk


def _import_all(db: duckdb.DuckDBPyConnection, client: LLMClient | None) -> None:
    for name, content, last4 in [
        ("card.csv", fixture_bytes("chase_card_jan.csv"), "0000"),
        ("chk.csv", fixture_bytes("chase_checking_jan.csv"), "0000"),
        ("p2p.csv", P2P_CHECKING, "1111"),
    ]:
        import_and_process(db, content, filename=name, last4=last4, llm=client)


def _sent_descriptions(sdk: FakeSDK) -> list[str]:
    return [m["description"] for p in sdk.user_payloads() for m in json.loads(p)["merchants"]]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("ZELLE PAYMENT TO JANE SAMPLE", True),
        ("VENMO PAYMENT", True),
        ("CASH APP*JANE", True),
        ("PAYPAL TRANSFER", True),
        ("SPOTIFY", False),
        ("SYNTH COFFEE CO", False),
    ],
)
def test_is_p2p(key: str, expected: bool) -> None:
    assert is_p2p(key) is expected


@pytest.mark.parametrize("provider", PROVIDERS)
def test_p2p_and_transfers_are_never_sent(db: duckdb.DuckDBPyConnection, provider: str) -> None:
    client, sdk = _client(db, provider)
    _import_all(db, client)
    sent = _sent_descriptions(sdk)
    assert "SYNTH GROCER" in sent
    assert not any("ZELLE" in d or "VENMO" in d or "JANE" in d for d in sent)
    # Card payment and its checking side are transfers: nothing to name.
    assert not any("PAYMENT TO CHASE" in d or "THANK YOU" in d for d in sent)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_names_and_llm_categories_are_applied(db: duckdb.DuckDBPyConnection, provider: str) -> None:
    client, _ = _client(db, provider)
    _import_all(db, client)
    merchant = db.execute(
        "SELECT clean_name, source, confidence FROM merchants "
        "WHERE normalized_key = 'SYNTH STREAMING'"
    ).fetchone()
    assert merchant == ("Synth Streaming", "llm", 0.9)
    # LLM default beats the Chase fallback (Entertainment) ...
    assert db.execute(
        "SELECT c.name, t.category_source FROM transactions t JOIN categories c "
        "ON c.id = t.category_id WHERE t.raw_description = 'SYNTH STREAMING'"
    ).fetchone() == ("Streaming Services", "llm")
    # ... but not a rule: the service fee stays with its seed rule.
    assert db.execute(
        "SELECT category_source FROM transactions WHERE raw_description = 'MONTHLY SERVICE FEE'"
    ).fetchone() == ("rule",)
    assert db.execute(
        "SELECT DISTINCT merchant FROM v_spend WHERE merchant LIKE 'Synth Streaming'"
    ).fetchall() == [("Synth Streaming",)]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_second_run_makes_zero_api_calls(db: duckdb.DuckDBPyConnection, provider: str) -> None:
    client, sdk = _client(db, provider)
    _import_all(db, client)
    calls = len(sdk.calls)
    assert calls > 0
    _import_all(db, client)  # same files again
    assert run_merchant_naming(db, client).sent == 0
    assert len(sdk.calls) == calls


@pytest.mark.parametrize("provider", PROVIDERS)
def test_user_choices_are_never_overwritten(db: duckdb.DuckDBPyConnection, provider: str) -> None:
    _import_all(db, None)  # local-only first
    (coffee,) = db.execute("SELECT id FROM categories WHERE name = 'Gifts'").fetchone() or (0,)
    db.execute(
        "UPDATE merchants SET clean_name = 'My Coffee', default_category_id = ?, source = 'user' "
        "WHERE normalized_key = 'SYNTH COFFEE CO'",
        [coffee],
    )
    client, sdk = _client(db, provider)
    run_merchant_naming(db, client)
    assert "SYNTH COFFEE CO" not in _sent_descriptions(sdk)
    assert db.execute(
        "SELECT clean_name, source FROM merchants WHERE normalized_key = 'SYNTH COFFEE CO'"
    ).fetchone() == ("My Coffee", "user")


@pytest.mark.parametrize("provider", PROVIDERS)
def test_failure_never_breaks_import_and_retries_later(
    db: duckdb.DuckDBPyConnection, provider: str
) -> None:
    client, sdk = _client(db, provider)
    sdk.responder = lambda _user: "not json"
    summary = import_and_process(
        db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000", llm=client
    )
    assert summary.imported.new_rows == 8
    assert summary.naming is not None
    assert summary.naming.failed
    assert db.execute("SELECT count(*) FROM merchants WHERE source = 'llm'").fetchone() == (0,)
    # Next run, with a working provider, names them.
    sdk.responder = default_responder
    assert run_merchant_naming(db, client).named > 0


def test_local_only_sends_nothing(db: duckdb.DuckDBPyConnection) -> None:
    summary = import_and_process(
        db, fixture_bytes("chase_card_jan.csv"), filename="c.csv", last4="0000", llm=None
    )
    assert summary.naming is None
    assert db.execute("SELECT count(*) FROM llm_calls").fetchone() == (0,)
