"""Fake provider SDKs: record every request, answer from a local responder. No network."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from anthropic.types import TextBlock

from spendsight.llm.providers.anthropic import AnthropicAdapter
from spendsight.llm.providers.openai import OpenAIAdapter
from spendsight.llm.types import ProviderAdapter

Responder = Callable[[str], str]  # user payload -> JSON text

KEYWORD_CATEGORIES = {
    "GROCER": "Groceries",
    "COFFEE": "Coffee Shops",
    "STREAMING": "Streaming Services",
}


def default_responder(user: str) -> str:
    """Echo every merchant: title-cased name, keyword-based category, else Unknown."""
    payload = json.loads(user)
    items = []
    for m in payload["merchants"]:
        desc = m["description"]
        category = next((c for k, c in KEYWORD_CATEGORIES.items() if k in desc), "Unknown")
        items.append(
            {"id": m["id"], "clean_name": desc.title(), "category": category, "confidence": 0.9}
        )
    return json.dumps({"merchants": items})


class _AnthropicMessages:
    def __init__(self, owner: FakeAnthropicSDK) -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> Any:
        self._owner.calls.append(kwargs)
        if self._owner.raise_error is not None:
            raise self._owner.raise_error
        text = self._owner.responder(kwargs["messages"][0]["content"])
        return SimpleNamespace(
            stop_reason=self._owner.stop_reason,
            content=[TextBlock.model_construct(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


class FakeAnthropicSDK:
    def __init__(self, responder: Responder = default_responder) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []
        self.stop_reason = "end_turn"
        self.raise_error: Exception | None = None
        self.messages = _AnthropicMessages(self)

    def user_payloads(self) -> list[str]:
        return [c["messages"][0]["content"] for c in self.calls]


class _OpenAIResponses:
    def __init__(self, owner: FakeOpenAISDK) -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> Any:
        self._owner.calls.append(kwargs)
        if self._owner.raise_error is not None:
            raise self._owner.raise_error
        text = self._owner.responder(kwargs["input"])
        return SimpleNamespace(
            status=self._owner.status,
            output=[SimpleNamespace(content=[SimpleNamespace(type=self._owner.part_type)])],
            output_text=text,
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


class FakeOpenAISDK:
    def __init__(self, responder: Responder = default_responder) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []
        self.status = "completed"
        self.part_type = "output_text"
        self.raise_error: Exception | None = None
        self.responses = _OpenAIResponses(self)

    def user_payloads(self) -> list[str]:
        return [c["input"] for c in self.calls]


FakeSDK = FakeAnthropicSDK | FakeOpenAISDK


def make_adapter(
    provider: str, responder: Responder = default_responder
) -> tuple[ProviderAdapter, FakeSDK]:
    if provider == "anthropic":
        a_sdk = FakeAnthropicSDK(responder)
        return AnthropicAdapter("test-key", sdk_client=a_sdk), a_sdk
    o_sdk = FakeOpenAISDK(responder)
    return OpenAIAdapter("test-key", sdk_client=o_sdk), o_sdk


PROVIDERS = ["anthropic", "openai"]
