"""Runtime settings, read from the environment (and `.env` when present)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_args

from dotenv import load_dotenv

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}

LlmProvider = Literal["anthropic", "openai"]
LLM_PROVIDERS: tuple[LlmProvider, ...] = get_args(LlmProvider)


class ConfigError(ValueError):
    """Raised when an environment variable holds an invalid value."""


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path("data/spendsight.duckdb")
    local_only: bool = False
    llm_provider: LlmProvider = "anthropic"
    llm_model: str | None = None
    ml_conf_threshold: float = 0.75
    # Keys are excluded from repr so they never end up in logs or tracebacks.
    anthropic_api_key: str | None = field(default=None, repr=False)
    openai_api_key: str | None = field(default=None, repr=False)
    # People's names masked in every outbound LLM payload (llm/redact.py). Personal data,
    # so kept out of repr too.
    redact_names: tuple[str, ...] = field(default=(), repr=False)

    @property
    def llm_api_key(self) -> str | None:
        """API key for the configured provider. Only `llm/` should read this."""
        if self.llm_provider == "openai":
            return self.openai_api_key
        return self.anthropic_api_key

    @property
    def llm_enabled(self) -> bool:
        """External LLM calls are allowed only when not local-only and fully configured."""
        return not self.local_only and bool(self.llm_api_key) and bool(self.llm_model)


def _parse_bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be true/false, got {raw!r}")


def _parse_provider(name: str, raw: str) -> LlmProvider:
    value = raw.strip().lower()
    for provider in LLM_PROVIDERS:
        if value == provider:
            return provider
    raise ConfigError(f"{name} must be one of {', '.join(LLM_PROVIDERS)}, got {raw!r}")


def _parse_threshold(name: str, raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if not 0.0 <= value <= 1.0:
        raise ConfigError(f"{name} must be between 0 and 1, got {value}")
    return value


def settings_from_env(env: Mapping[str, str]) -> Settings:
    """Build settings from an explicit mapping. Pure, so tests don't touch os.environ."""
    defaults = Settings()
    return Settings(
        db_path=Path(env.get("SPENDSIGHT_DB_PATH") or defaults.db_path),
        local_only=_parse_bool("SPENDSIGHT_LOCAL_ONLY", env.get("SPENDSIGHT_LOCAL_ONLY", "")),
        llm_provider=_parse_provider(
            "SPENDSIGHT_LLM_PROVIDER", env.get("SPENDSIGHT_LLM_PROVIDER") or defaults.llm_provider
        ),
        llm_model=env.get("SPENDSIGHT_LLM_MODEL") or None,
        ml_conf_threshold=_parse_threshold(
            "SPENDSIGHT_ML_CONF_THRESHOLD",
            env.get("SPENDSIGHT_ML_CONF_THRESHOLD") or str(defaults.ml_conf_threshold),
        ),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY") or None,
        openai_api_key=env.get("OPENAI_API_KEY") or None,
        redact_names=tuple(
            name.strip()
            for name in env.get("SPENDSIGHT_REDACT_NAMES", "").split(",")
            if name.strip()
        ),
    )


def load_settings(dotenv_path: Path | None = None) -> Settings:
    """Load `.env` (without overriding real env vars) and build settings."""
    load_dotenv(dotenv_path=dotenv_path, override=False)
    return settings_from_env(os.environ)
