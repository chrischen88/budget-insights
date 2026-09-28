"""The single entry point for model calls (SPEC.md §7).

Every call runs: redaction -> cache -> provider call -> schema validation -> usage log.
Features describe what they want (prompt, content, schema, pydantic model); they never
see which provider answers. No automatic failover: a failed call raises LLMError.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from importlib import resources
from typing import Any, TypeVar

import duckdb
from pydantic import BaseModel, ValidationError

from spendsight.config import Settings
from spendsight.db import repository
from spendsight.llm.redact import Redactor
from spendsight.llm.types import LLMError, ProviderAdapter

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str


def load_prompt(name: str, version: int) -> Prompt:
    """Read llm/prompts/<name>.v<version>.md. Changing a prompt means a new version file."""
    path = resources.files("spendsight.llm") / "prompts" / f"{name}.v{version}.md"
    return Prompt(name, version, path.read_text(encoding="utf-8"))


class LLMClient:
    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        provider: ProviderAdapter,
        model: str,
        redactor: Redactor,
    ) -> None:
        self._conn = conn
        self._provider = provider
        self._model = model
        self._redactor = redactor

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def _cache_key(self, prompt: Prompt, user: str, schema: dict[str, Any]) -> str:
        material = json.dumps(
            [self._provider.name, self._model, prompt.name, prompt.version, schema, user],
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _log(
        self,
        prompt: Prompt,
        *,
        cache_hit: bool,
        ok: bool,
        error: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        repository.log_llm_call(
            self._conn,
            provider=self._provider.name,
            model=self._model,
            prompt=prompt.name,
            prompt_version=prompt.version,
            cache_hit=cache_hit,
            ok=ok,
            error=error,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def structured(
        self,
        prompt: Prompt,
        user_content: str,
        *,
        schema: dict[str, Any],
        output_model: type[T],
        max_tokens: int = 8000,
    ) -> T:
        user = self._redactor.redact(user_content)
        key = self._cache_key(prompt, user, schema)
        cached = repository.llm_cache_get(self._conn, key)
        if cached is not None:
            try:
                parsed = output_model.model_validate_json(cached)
            except ValidationError:
                log.warning("ignoring invalid cached %s response", prompt.name)
            else:
                self._log(prompt, cache_hit=True, ok=True)
                return parsed

        try:
            result = self._provider.complete_json(
                model=self._model,
                system=prompt.text,
                user=user,
                schema=schema,
                schema_name=prompt.name,
                max_tokens=max_tokens,
            )
        except LLMError as exc:
            self._log(prompt, cache_hit=False, ok=False, error=str(exc)[:200])
            raise
        try:
            parsed = output_model.model_validate_json(result.text)
        except ValidationError as exc:
            self._log(
                prompt,
                cache_hit=False,
                ok=False,
                error="schema validation failed",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            raise LLMError(f"{prompt.name} response failed validation") from exc

        repository.llm_cache_put(self._conn, key, result.text)
        self._log(
            prompt,
            cache_hit=False,
            ok=True,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        return parsed


def build_client(settings: Settings, conn: duckdb.DuckDBPyConnection) -> LLMClient | None:
    """The configured client, or None when LLM features are off (local-only or unset)."""
    if not settings.llm_enabled or settings.llm_model is None or settings.llm_api_key is None:
        return None
    provider: ProviderAdapter
    if settings.llm_provider == "openai":
        from spendsight.llm.providers.openai import OpenAIAdapter

        provider = OpenAIAdapter(settings.llm_api_key)
    else:
        from spendsight.llm.providers.anthropic import AnthropicAdapter

        provider = AnthropicAdapter(settings.llm_api_key)
    return LLMClient(conn, provider, settings.llm_model, Redactor(settings.redact_names))
