import anthropic
import openai
import pytest

from spendsight.llm.types import LLMError
from tests.llm.fakes import FakeAnthropicSDK, FakeOpenAISDK, make_adapter

SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def _call(adapter: object) -> None:
    adapter.complete_json(  # type: ignore[attr-defined]
        model="configured-model",
        system="SYSTEM",
        user='{"merchants": []}',
        schema=SCHEMA,
        schema_name="merchant_normalize",
        max_tokens=1000,
    )


def test_anthropic_request_shape() -> None:
    adapter, sdk = make_adapter("anthropic")
    _call(adapter)
    (call,) = sdk.calls
    assert call["model"] == "configured-model"
    assert call["system"] == "SYSTEM"
    assert call["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert call["messages"] == [{"role": "user", "content": '{"merchants": []}'}]


def test_openai_request_shape() -> None:
    adapter, sdk = make_adapter("openai")
    _call(adapter)
    (call,) = sdk.calls
    assert call["model"] == "configured-model"
    assert call["instructions"] == "SYSTEM"
    assert call["input"] == '{"merchants": []}'
    assert call["text"]["format"] == {
        "type": "json_schema",
        "name": "merchant_normalize",
        "schema": SCHEMA,
        "strict": True,
    }


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_anthropic_unusable_stop_reasons(stop_reason: str) -> None:
    adapter, sdk = make_adapter("anthropic")
    assert isinstance(sdk, FakeAnthropicSDK)
    sdk.stop_reason = stop_reason
    with pytest.raises(LLMError):
        _call(adapter)


def test_openai_incomplete_or_refused() -> None:
    adapter, sdk = make_adapter("openai")
    assert isinstance(sdk, FakeOpenAISDK)
    sdk.status = "incomplete"
    with pytest.raises(LLMError, match="not completed"):
        _call(adapter)
    sdk.status, sdk.part_type = "completed", "refusal"
    with pytest.raises(LLMError, match="declined"):
        _call(adapter)


def test_sdk_errors_become_llm_errors_without_payload() -> None:
    a_adapter, a_sdk = make_adapter("anthropic")
    a_sdk.raise_error = anthropic.APIConnectionError.__new__(anthropic.APIConnectionError)
    with pytest.raises(LLMError, match="APIConnectionError") as a_exc:
        _call(a_adapter)
    assert "merchants" not in str(a_exc.value)

    o_adapter, o_sdk = make_adapter("openai")
    o_sdk.raise_error = openai.OpenAIError("boom")
    with pytest.raises(LLMError, match="OpenAIError"):
        _call(o_adapter)
