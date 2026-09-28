"""Anthropic adapter: JSON-schema structured output via the Messages API."""

from __future__ import annotations

from typing import Any

import anthropic
from anthropic.types import TextBlock

from spendsight.llm.types import LLMError, ProviderResult

REQUEST_TIMEOUT_SECONDS = 120.0


class AnthropicAdapter:
    name = "anthropic"

    def __init__(self, api_key: str, *, sdk_client: Any = None) -> None:
        # sdk_client is injectable so tests never touch the network.
        self._client = sdk_client or anthropic.Anthropic(
            api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=2
        )

    def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int,
    ) -> ProviderResult:
        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APIError as exc:
            raise LLMError(f"anthropic request failed: {type(exc).__name__}") from exc
        if response.stop_reason == "refusal":
            raise LLMError("anthropic declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError("anthropic response was truncated (max_tokens)")
        text = next((b.text for b in response.content if isinstance(b, TextBlock)), None)
        if text is None:
            raise LLMError("anthropic response had no text block")
        return ProviderResult(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
