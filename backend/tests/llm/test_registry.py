"""`llm/registry.py` tests — provider/model selection from config/env only, role-based
routing, and graceful degradation when a provider's key isn't configured.
"""

from __future__ import annotations

import socket

import pytest

from config.settings import Settings
from llm.anthropic_client import AnthropicClient
from llm.gemini_client import GeminiClient
from llm.local_client import LocalOllamaClient
from llm.openai_client import OpenAIClient
from llm.openai_compat import OpenAICompatClient
from llm.registry import (
    ModelRole,
    ProviderNotConfiguredError,
    build_client,
    default_client,
    resolve_role,
)


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def test_build_client_anthropic() -> None:
    settings = _settings(anthropic_api_key="sk-ant-test")
    client = build_client("anthropic", "claude-sonnet-5", settings)
    assert isinstance(client, AnthropicClient)
    assert client.model_id == "claude-sonnet-5"


def test_build_client_ollama_needs_no_key() -> None:
    settings = _settings(ollama_base_url="http://localhost:11434")
    client = build_client("ollama", "qwen3.6", settings)
    assert isinstance(client, LocalOllamaClient)


@pytest.mark.parametrize("provider_id", ["groq", "cerebras", "openrouter"])
def test_build_client_openai_compat_providers(provider_id: str) -> None:
    settings = _settings(groq_api_key="k", cerebras_api_key="k", openrouter_api_key="k")
    client = build_client(provider_id, "some-model", settings)
    assert isinstance(client, OpenAICompatClient)
    assert client.provider_id == provider_id


def test_build_client_raises_when_key_missing() -> None:
    settings = _settings(anthropic_api_key=None)
    with pytest.raises(ProviderNotConfiguredError, match="anthropic_api_key"):
        build_client("anthropic", "claude-sonnet-5", settings)


def test_build_client_raises_when_openai_key_missing() -> None:
    settings = _settings(openai_api_key=None)
    with pytest.raises(ProviderNotConfiguredError, match="openai_api_key"):
        build_client("openai", "gpt-5.5", settings)


def test_build_client_raises_when_gemini_key_missing() -> None:
    settings = _settings(gemini_api_key=None)
    with pytest.raises(ProviderNotConfiguredError, match="gemini_api_key"):
        build_client("gemini", "gemini-3.5-flash", settings)


def test_build_client_openai() -> None:
    settings = _settings(openai_api_key="sk-test")
    client = build_client("openai", "gpt-5.5", settings)
    assert isinstance(client, OpenAIClient)
    assert client.provider_id == "openai"
    assert client.model_id == "gpt-5.5"


def test_build_client_gemini() -> None:
    settings = _settings(gemini_api_key="gem-test")
    client = build_client("gemini", "gemini-3.5-flash", settings)
    assert isinstance(client, GeminiClient)
    assert client.provider_id == "gemini"
    assert client.model_id == "gemini-3.5-flash"


def test_build_client_raises_for_unimplemented_provider() -> None:
    settings = _settings()
    with pytest.raises(ProviderNotConfiguredError, match="no LLMClient implementation"):
        build_client("bedrock", "some-model", settings)


def test_default_client_falls_back_to_anthropic_when_unset() -> None:
    settings = _settings(anthropic_api_key="sk-ant-test")
    client = default_client(settings)
    assert client.provider_id == "anthropic"
    assert client.model_id == "claude-sonnet-5"


def test_default_client_honors_explicit_override() -> None:
    settings = _settings(
        aitw_llm_provider="ollama", aitw_llm_model="qwen3.6", ollama_base_url="http://x:1"
    )
    client = default_client(settings)
    assert client.provider_id == "ollama"
    assert client.model_id == "qwen3.6"


def test_resolve_role_uses_suggested_default_for_classifier() -> None:
    settings = _settings(groq_api_key="k")
    client = resolve_role(ModelRole.CLASSIFIER, settings)
    assert client.provider_id == "groq"
    assert client.model_id == "llama-3.3-70b-versatile"


def test_resolve_role_honors_override() -> None:
    settings = _settings(aitw_model_role_classifier="ollama/qwen3.6", ollama_base_url="http://x:1")
    client = resolve_role(ModelRole.CLASSIFIER, settings)
    assert client.provider_id == "ollama"
    assert client.model_id == "qwen3.6"


def test_offline_profile_routes_every_role_to_ollama() -> None:
    """The offline profile routes every role to ollama/qwen3.6 — purely config, no code
    change, since provider/model selection is never hardcoded in core logic."""
    settings = _settings(
        aitw_model_role_classifier="ollama/qwen3.6",
        aitw_model_role_agent="ollama/qwen3.6",
        aitw_model_role_subagent="ollama/qwen3.6",
        aitw_model_role_synthesis="ollama/qwen3.6",
        ollama_base_url="http://x:1",
    )
    for role in ModelRole:
        client = resolve_role(role, settings)
        assert client.provider_id == "ollama"


def test_resolve_role_raises_when_role_provider_unconfigured() -> None:
    settings = _settings(cerebras_api_key=None)
    with pytest.raises(ProviderNotConfiguredError):
        resolve_role(ModelRole.SUBAGENT, settings)


def test_construction_never_touches_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """No client constructed via the registry should ever open a socket — only chat()/
    stream()/count_tokens() do."""

    def _blow_up(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket opened during client construction")

    monkeypatch.setattr(socket, "socket", _blow_up)
    settings = _settings(
        anthropic_api_key="k",
        openai_api_key="k",
        gemini_api_key="k",
        groq_api_key="k",
        ollama_base_url="http://localhost:11434",
    )
    build_client("anthropic", "claude-sonnet-5", settings)
    build_client("openai", "gpt-5.5", settings)
    build_client("gemini", "gemini-3.5-flash", settings)
    build_client("groq", "llama-3.3-70b-versatile", settings)
    build_client("ollama", "qwen3.6", settings)
