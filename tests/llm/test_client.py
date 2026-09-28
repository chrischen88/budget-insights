"""LLMClient behaviors, each run against both provider adapters (mocked SDKs)."""

import json

import duckdb
import pytest
from pydantic import BaseModel

from spendsight.config import settings_from_env
from spendsight.llm.client import LLMClient, Prompt, build_client
from spendsight.llm.providers.anthropic import AnthropicAdapter
from spendsight.llm.providers.openai import OpenAIAdapter
from spendsight.llm.redact import Redactor
from spendsight.llm.types import LLMError
from tests.llm.fakes import PROVIDERS, make_adapter

PROMPT = Prompt("merchant_normalize", 1, "SYSTEM PROMPT")
SCHEMA = {"type": "object"}


class Out(BaseModel):
    merchants: list[dict[str, object]]


def _payload(*descriptions: str) -> str:
    return json.dumps(
        {"merchants": [{"id": i, "description": d} for i, d in enumerate(descriptions)]}
    )


def _client(
    db: duckdb.DuckDBPyConnection, provider: str, *, names: tuple[str, ...] = (), model: str = "m1"
) -> tuple[LLMClient, object]:
    adapter, sdk = make_adapter(provider)
    return LLMClient(db, adapter, model, Redactor(names)), sdk


def _calls(db: duckdb.DuckDBPyConnection) -> list[tuple[object, ...]]:
    return db.execute(
        "SELECT provider, model, prompt, prompt_version, cache_hit, ok, error FROM llm_calls "
        "ORDER BY id"
    ).fetchall()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_redaction_runs_on_outbound_payload(db: duckdb.DuckDBPyConnection, provider: str) -> None:
    client, sdk = _client(db, provider, names=("Jane Sample",))
    client.structured(
        PROMPT,
        _payload("ZELLE TO JANE SAMPLE 12345678", "a@b.com"),
        schema=SCHEMA,
        output_model=Out,
    )
    (sent,) = sdk.user_payloads()  # type: ignore[attr-defined]
    assert "JANE SAMPLE" not in sent
    assert "12345678" not in sent
    assert "a@b.com" not in sent
    assert "[NAME]" in sent


@pytest.mark.parametrize("provider", PROVIDERS)
def test_second_identical_call_is_served_from_cache(
    db: duckdb.DuckDBPyConnection, provider: str
) -> None:
    client, sdk = _client(db, provider)
    first = client.structured(PROMPT, _payload("SYNTH GROCER"), schema=SCHEMA, output_model=Out)
    second = client.structured(PROMPT, _payload("SYNTH GROCER"), schema=SCHEMA, output_model=Out)
    assert first == second
    assert len(sdk.calls) == 1  # type: ignore[attr-defined]
    assert [r[4:6] for r in _calls(db)] == [(False, True), (True, True)]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_cache_is_keyed_by_model_and_prompt_version(
    db: duckdb.DuckDBPyConnection, provider: str
) -> None:
    c1, sdk1 = _client(db, provider, model="m1")
    c2, sdk2 = _client(db, provider, model="m2")
    c1.structured(PROMPT, _payload("X"), schema=SCHEMA, output_model=Out)
    c2.structured(PROMPT, _payload("X"), schema=SCHEMA, output_model=Out)
    c1.structured(
        Prompt(PROMPT.name, 2, PROMPT.text), _payload("X"), schema=SCHEMA, output_model=Out
    )
    assert (len(sdk1.calls), len(sdk2.calls)) == (2, 1)  # type: ignore[attr-defined]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_invalid_response_is_rejected_and_not_cached(
    db: duckdb.DuckDBPyConnection, provider: str
) -> None:
    adapter, sdk = make_adapter(provider, responder=lambda _: '{"wrong": true}')
    client = LLMClient(db, adapter, "m1", Redactor())
    for _ in range(2):
        with pytest.raises(LLMError, match="failed validation"):
            client.structured(PROMPT, _payload("X"), schema=SCHEMA, output_model=Out)
    assert len(sdk.calls) == 2  # not cached, so retried
    assert db.execute("SELECT count(*) FROM llm_cache").fetchone() == (0,)
    assert [r[5:] for r in _calls(db)] == [(False, "schema validation failed")] * 2


@pytest.mark.parametrize("provider", PROVIDERS)
def test_provider_failure_is_logged_and_raised(
    db: duckdb.DuckDBPyConnection, provider: str
) -> None:
    client, sdk = _client(db, provider)
    sdk.raise_error = RuntimeError("not an SDK error")  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        client.structured(PROMPT, _payload("X"), schema=SCHEMA, output_model=Out)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_usage_log_never_stores_content(db: duckdb.DuckDBPyConnection, provider: str) -> None:
    client, _ = _client(db, provider)
    client.structured(PROMPT, _payload("SYNTH GROCER"), schema=SCHEMA, output_model=Out)
    (row,) = db.execute("SELECT * FROM llm_calls").fetchall()
    assert not any(isinstance(v, str) and "GROCER" in v for v in row)
    (tokens,) = db.execute("SELECT input_tokens + output_tokens FROM llm_calls").fetchone() or (0,)
    assert tokens == 150


def test_build_client_respects_settings(db: duckdb.DuckDBPyConnection) -> None:
    base = {"SPENDSIGHT_LLM_MODEL": "some-model"}
    assert build_client(settings_from_env(base), db) is None  # no key
    local = {**base, "ANTHROPIC_API_KEY": "k", "SPENDSIGHT_LOCAL_ONLY": "true"}
    assert build_client(settings_from_env(local), db) is None
    anth = build_client(settings_from_env({**base, "ANTHROPIC_API_KEY": "k"}), db)
    assert anth is not None
    assert anth.provider_name == AnthropicAdapter.name
    oai = build_client(
        settings_from_env({**base, "SPENDSIGHT_LLM_PROVIDER": "openai", "OPENAI_API_KEY": "k"}),
        db,
    )
    assert oai is not None
    assert oai.provider_name == OpenAIAdapter.name
