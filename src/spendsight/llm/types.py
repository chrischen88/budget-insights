"""Types shared by the LLM client and provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(Exception):
    """An LLM call failed or returned unusable output. Messages never include payloads."""


@dataclass(frozen=True)
class ProviderResult:
    text: str  # the JSON document the model returned
    input_tokens: int | None
    output_tokens: int | None


class ProviderAdapter(Protocol):
    """One provider's SDK behind a provider-neutral call. Only adapters import SDKs."""

    name: str

    def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int,
    ) -> ProviderResult: ...
