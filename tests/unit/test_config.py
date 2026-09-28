from pathlib import Path

import pytest

from spendsight.config import ConfigError, Settings, settings_from_env


def test_defaults_from_empty_env() -> None:
    s = settings_from_env({})
    assert s == Settings()
    assert s.db_path == Path("data/spendsight.duckdb")
    assert s.ml_conf_threshold == 0.75
    assert not s.llm_enabled


def test_full_env() -> None:
    s = settings_from_env(
        {
            "ANTHROPIC_API_KEY": "sk-test",
            "SPENDSIGHT_LLM_MODEL": "some-model",
            "SPENDSIGHT_LOCAL_ONLY": "false",
            "SPENDSIGHT_DB_PATH": "/tmp/x.duckdb",
            "SPENDSIGHT_ML_CONF_THRESHOLD": "0.9",
        }
    )
    assert s.db_path == Path("/tmp/x.duckdb")
    assert s.ml_conf_threshold == 0.9
    assert s.llm_enabled


def test_local_only_disables_llm() -> None:
    s = settings_from_env(
        {
            "ANTHROPIC_API_KEY": "sk-test",
            "SPENDSIGHT_LLM_MODEL": "some-model",
            "SPENDSIGHT_LOCAL_ONLY": "TRUE",
        }
    )
    assert s.local_only
    assert not s.llm_enabled


def test_llm_requires_model() -> None:
    assert not settings_from_env({"ANTHROPIC_API_KEY": "sk-test"}).llm_enabled


def test_api_key_not_in_repr() -> None:
    s = settings_from_env({"ANTHROPIC_API_KEY": "sk-secret-value"})
    assert "sk-secret-value" not in repr(s)


@pytest.mark.parametrize(
    "env",
    [
        {"SPENDSIGHT_LOCAL_ONLY": "maybe"},
        {"SPENDSIGHT_ML_CONF_THRESHOLD": "high"},
        {"SPENDSIGHT_ML_CONF_THRESHOLD": "1.5"},
    ],
)
def test_invalid_values(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        settings_from_env(env)


def test_default_provider_is_anthropic() -> None:
    assert settings_from_env({}).llm_provider == "anthropic"


def test_openai_provider_uses_openai_key() -> None:
    s = settings_from_env(
        {
            "SPENDSIGHT_LLM_PROVIDER": "OpenAI",
            "SPENDSIGHT_LLM_MODEL": "some-model",
            "OPENAI_API_KEY": "sk-openai",
            "ANTHROPIC_API_KEY": "sk-anthropic",
        }
    )
    assert s.llm_provider == "openai"
    assert s.llm_api_key == "sk-openai"
    assert s.llm_enabled


def test_provider_key_must_match_provider() -> None:
    # An Anthropic key alone does not enable the OpenAI provider, and vice versa.
    openai_without_key = settings_from_env(
        {
            "SPENDSIGHT_LLM_PROVIDER": "openai",
            "SPENDSIGHT_LLM_MODEL": "some-model",
            "ANTHROPIC_API_KEY": "sk-anthropic",
        }
    )
    assert not openai_without_key.llm_enabled
    anthropic_without_key = settings_from_env(
        {"SPENDSIGHT_LLM_MODEL": "some-model", "OPENAI_API_KEY": "sk-openai"}
    )
    assert not anthropic_without_key.llm_enabled


def test_local_only_disables_openai() -> None:
    s = settings_from_env(
        {
            "SPENDSIGHT_LLM_PROVIDER": "openai",
            "SPENDSIGHT_LLM_MODEL": "some-model",
            "OPENAI_API_KEY": "sk-openai",
            "SPENDSIGHT_LOCAL_ONLY": "true",
        }
    )
    assert not s.llm_enabled


def test_openai_key_not_in_repr() -> None:
    s = settings_from_env({"OPENAI_API_KEY": "sk-openai-secret"})
    assert "sk-openai-secret" not in repr(s)


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ConfigError):
        settings_from_env({"SPENDSIGHT_LLM_PROVIDER": "gemini"})


def test_redact_names_parsed_and_hidden() -> None:
    s = settings_from_env({"SPENDSIGHT_REDACT_NAMES": " Jane Sample, ,Bob "})
    assert s.redact_names == ("Jane Sample", "Bob")
    assert "Jane" not in repr(s)
    assert settings_from_env({}).redact_names == ()
