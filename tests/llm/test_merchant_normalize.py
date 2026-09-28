import json

import duckdb
import pytest

from spendsight.llm.client import LLMClient
from spendsight.llm.merchant_normalize import (
    BATCH_SIZE,
    UNKNOWN,
    MerchantSuggestion,
    response_schema,
    suggest_merchants,
)
from spendsight.llm.redact import Redactor
from tests.llm.fakes import PROVIDERS, FakeSDK, Responder, default_responder, make_adapter

CATEGORIES = {"Groceries": 10, "Coffee Shops": 20}


def _client(
    db: duckdb.DuckDBPyConnection, provider: str, responder: Responder = default_responder
) -> tuple[LLMClient, FakeSDK]:
    adapter, sdk = make_adapter(provider, responder)
    return LLMClient(db, adapter, "m1", Redactor()), sdk


def _fixed(items: list[dict[str, object]]) -> Responder:
    return lambda _user: json.dumps({"merchants": items})


def test_schema_limits_categories_to_ours_plus_unknown() -> None:
    enum = response_schema(["Groceries"])["properties"]["merchants"]["items"]["properties"][
        "category"
    ]["enum"]
    assert enum == ["Groceries", UNKNOWN]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_suggestions_map_back_to_keys(db: duckdb.DuckDBPyConnection, provider: str) -> None:
    client, _ = _client(db, provider)
    out = suggest_merchants(client, ["SYNTH GROCER", "SYNTH COFFEE CO", "ACME"], CATEGORIES)
    assert out == [
        MerchantSuggestion("SYNTH GROCER", "Synth Grocer", 10, 0.9),
        MerchantSuggestion("SYNTH COFFEE CO", "Synth Coffee Co", 20, 0.9),
        MerchantSuggestion("ACME", "Acme", None, None),  # Unknown -> no category
    ]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_invalid_items_are_dropped(db: duckdb.DuckDBPyConnection, provider: str) -> None:
    responder = _fixed(
        [
            {"id": 0, "clean_name": "Grocer", "category": "Not A Category", "confidence": 0.9},
            {"id": 0, "clean_name": "Duplicate", "category": "Groceries", "confidence": 0.9},
            {"id": 7, "clean_name": "No Such Id", "category": "Groceries", "confidence": 0.9},
            {"id": 1, "clean_name": "Payment [NUMBER]", "category": "Groceries", "confidence": 0.4},
        ]
    )
    client, _ = _client(db, provider, responder)
    out = suggest_merchants(client, ["A", "B"], CATEGORIES)
    assert out == [
        MerchantSuggestion("A", "Grocer", None, None),  # invalid category never kept
        MerchantSuggestion("B", None, 10, 0.4),  # placeholder in name -> no name
    ]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_batches_of_fifty(db: duckdb.DuckDBPyConnection, provider: str) -> None:
    client, sdk = _client(db, provider)
    keys = [f"MERCHANT {i}" for i in range(BATCH_SIZE * 2 + 20)]
    out = suggest_merchants(client, keys, CATEGORIES)
    assert len(sdk.calls) == 3
    assert [s.key for s in out] == keys


@pytest.mark.parametrize("provider", PROVIDERS)
def test_payload_has_no_amounts_or_dates(db: duckdb.DuckDBPyConnection, provider: str) -> None:
    client, sdk = _client(db, provider)
    suggest_merchants(client, ["SYNTH GROCER"], CATEGORIES)
    (sent,) = sdk.user_payloads()
    assert json.loads(sent) == {
        "categories": ["Coffee Shops", "Groceries"],
        "merchants": [{"id": 0, "description": "SYNTH GROCER"}],
    }
