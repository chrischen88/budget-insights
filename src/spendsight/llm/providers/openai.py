"""OpenAI adapter: strict JSON-schema structured output via the Responses API."""

from __future__ import annotations

from typing import Any

import openai

from spendsight.llm.types import LLMError, ProviderResult

REQUEST_TIMEOUT_SECONDS = 120.0


class OpenAIAdapter:
    name = "openai"

    def __init__(self, api_key: str, *, sdk_client: Any = None) -> None:
        # sdk_client is injectable so tests never touch the network.
        self._client = sdk_client or openai.OpenAI(
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
            response = self._client.responses.create(
                model=model,
                instructions=system,
                input=user,
                max_output_tokens=max_tokens,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    }
                },
            )
        except openai.OpenAIError as exc:
            raise LLMError(f"openai request failed: {type(exc).__name__}") from exc
        if response.status != "completed":
            raise LLMError(f"openai response not completed ({response.status})")
        for item in response.output:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "refusal":
                    raise LLMError("openai declined the request")
        text = response.output_text
        if not text:
            raise LLMError("openai response had no text output")
        usage = response.usage
        return ProviderResult(
            text=text,
            input_tokens=None if usage is None else usage.input_tokens,
            output_tokens=None if usage is None else usage.output_tokens,
        )
