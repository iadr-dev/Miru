"""`llm/registry.py` tests — provider/model selection from config/env only, role-based
routing, and graceful degradation when a provider's key isn't configured.
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator

import pytest

from config.settings import Settings
from llm.anthropic_client import AnthropicClient
from llm.base import (
    LLMEvent,
    LLMResponse,
    Message,
    MessageRole,
    ModelCapabilities,
    StopReason,
    TokenUsage,
    ToolSpec,
)
from llm.gemini_client import GeminiClient
from llm.local_client import LocalModelClient
from llm.openai_client import OpenAIClient
from llm.openai_compat import OpenAICompatClient
from llm.registry import (
    FallbackLLMClient,
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


def test_build_client_threads_llm_max_output_tokens() -> None:
    """Regression guard: a live earnings-quality turn was observed getting cut off mid-sentence
    because every client fell back to its own conservative 4096-token constructor default —
    `build_client` must pass `Settings.llm_max_output_tokens` through, not silently drop it."""
    settings = _settings(
        anthropic_api_key="sk-ant-test",
        openai_api_key="sk-test",
        gemini_api_key="gem-test",
        llm_max_output_tokens=16_000,
    )
    anthropic_client = build_client("anthropic", "claude-sonnet-5", settings)
    openai_client = build_client("openai", "gpt-5.5", settings)
    gemini_client = build_client("gemini", "gemini-3.5-flash", settings)
    assert isinstance(anthropic_client, AnthropicClient)
    assert isinstance(openai_client, OpenAIClient)
    assert isinstance(gemini_client, GeminiClient)
    assert anthropic_client._max_tokens == 16_000
    assert openai_client._max_tokens == 16_000
    assert gemini_client._max_output_tokens == 16_000


def test_build_client_local_needs_no_key() -> None:
    settings = _settings(local_llm_base_url="http://localhost:12434/engines")
    client = build_client("local", "qwen3.6", settings)
    assert isinstance(client, LocalModelClient)


def test_local_client_targets_the_configured_base_url() -> None:
    settings = _settings(local_llm_base_url="http://runner.internal/engines")
    client = build_client("local", "ai/qwen3.6:latest", settings)
    assert isinstance(client, LocalModelClient)
    assert client.provider_id == "local"
    assert client.model_id == "ai/qwen3.6:latest"


def test_unregistered_provider_id_is_rejected() -> None:
    with pytest.raises(ProviderNotConfiguredError, match="no LLMClient implementation"):
        build_client("retired-local-provider", "qwen3.6", _settings())


def test_fallback_chain_resolves_a_local_entry_with_a_namespaced_model_id() -> None:
    settings = _settings(
        model_role_agent="anthropic/claude-sonnet-5,local/ai/qwen3.6:latest",
        anthropic_api_key="sk-ant-test",
    )
    client = resolve_role(ModelRole.AGENT, settings)
    assert isinstance(client, FallbackLLMClient)
    assert client.provider_id == "anthropic"


@pytest.mark.parametrize("provider_id", ["openrouter"])
def test_build_client_openai_compat_providers(provider_id: str) -> None:
    settings = _settings(openrouter_api_key="k")
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
    assert client.model_id == "claude-sonnet-5-5"


def test_default_client_honors_explicit_override() -> None:
    settings = _settings(llm_provider="local", llm_model="qwen3.6", local_llm_base_url="http://x:1")
    client = default_client(settings)
    assert client.provider_id == "local"
    assert client.model_id == "qwen3.6"


def test_resolve_role_uses_suggested_default_for_classifier() -> None:
    settings = _settings(anthropic_api_key="k")
    client = resolve_role(ModelRole.CLASSIFIER, settings)
    assert client.provider_id == "anthropic"
    assert client.model_id == "claude-haiku-5-5"


def test_synthesis_uses_openai_first_and_falls_back_to_anthropic() -> None:
    both = resolve_role(ModelRole.SYNTHESIS, _settings(openai_api_key="k", anthropic_api_key="k"))
    assert (both.provider_id, both.model_id) == ("openai", "gpt-4o-mini")


def test_synthesis_without_an_openai_key_runs_on_anthropic_instead_of_failing() -> None:
    client = resolve_role(
        ModelRole.SYNTHESIS, _settings(openai_api_key=None, anthropic_api_key="k")
    )
    assert (client.provider_id, client.model_id) == ("anthropic", "claude-sonnet-5-5")


def test_resolve_role_honors_override() -> None:
    settings = _settings(model_role_classifier="local/qwen3.6", local_llm_base_url="http://x:1")
    client = resolve_role(ModelRole.CLASSIFIER, settings)
    assert client.provider_id == "local"
    assert client.model_id == "qwen3.6"


def test_offline_profile_routes_every_role_to_local_model() -> None:
    """The offline profile routes every role to local/qwen3.6 — purely config, no code
    change, since provider/model selection is never hardcoded in core logic."""
    settings = _settings(
        model_role_classifier="local/qwen3.6",
        model_role_agent="local/qwen3.6",
        model_role_subagent="local/qwen3.6",
        model_role_synthesis="local/qwen3.6",
        local_llm_base_url="http://x:1",
    )
    for role in ModelRole:
        client = resolve_role(role, settings)
        assert client.provider_id == "local"


def test_resolve_role_raises_when_role_provider_unconfigured() -> None:
    settings = _settings(anthropic_api_key=None)
    with pytest.raises(ProviderNotConfiguredError):
        resolve_role(ModelRole.SUBAGENT, settings)


def test_resolve_role_override_with_single_entry_returns_plain_client() -> None:
    """A single-entry override must return the concrete client directly, not a
    `FallbackLLMClient` wrapper — no fallback machinery for the common one-model case."""
    settings = _settings(model_role_agent="local/qwen3.6", local_llm_base_url="http://x:1")
    client = resolve_role(ModelRole.AGENT, settings)
    assert isinstance(client, LocalModelClient)


def test_resolve_role_override_with_multiple_entries_returns_fallback_client() -> None:
    settings = _settings(
        model_role_agent="anthropic/claude-sonnet-5,local/qwen3.6",
        anthropic_api_key="sk-ant-test",
        local_llm_base_url="http://x:1",
    )
    client = resolve_role(ModelRole.AGENT, settings)
    assert isinstance(client, FallbackLLMClient)
    assert client.provider_id == "anthropic"
    assert client.model_id == "claude-sonnet-5"


def test_resolve_role_chain_skips_unconfigured_entries() -> None:
    """A multi-entry chain with one unconfigured provider degrades to the remaining
    configured entries rather than failing the whole role."""
    settings = _settings(
        model_role_agent="openai/gpt-5.5,local/qwen3.6",
        openai_api_key=None,
        local_llm_base_url="http://x:1",
    )
    client = resolve_role(ModelRole.AGENT, settings)
    assert isinstance(client, LocalModelClient)


def test_resolve_role_chain_raises_when_every_entry_unconfigured() -> None:
    settings = _settings(
        model_role_agent="openai/gpt-5.5,gemini/gemini-3.5-flash",
        openai_api_key=None,
        gemini_api_key=None,
    )
    with pytest.raises(ProviderNotConfiguredError):
        resolve_role(ModelRole.AGENT, settings)


class _ScriptedClient:
    """A minimal `LLMClient` stub that always raises (simulating a provider outage/429) or
    always succeeds, for exercising `FallbackLLMClient`'s try-next behavior."""

    def __init__(self, provider_id: str, *, fails: bool) -> None:
        self.provider_id = provider_id
        self.model_id = "stub-model"
        self.capabilities = ModelCapabilities()
        self._fails = fails

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        del messages, tools, native_web_search
        if self._fails:
            raise RuntimeError(f"{self.provider_id} is down")
        return LLMResponse(
            text=f"answer from {self.provider_id}",
            tool_calls=(),
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[LLMEvent]:
        del messages, tools, native_web_search
        if self._fails:
            raise RuntimeError(f"{self.provider_id} is down")
        return
        yield  # pragma: no cover — makes this an async generator; unreachable after `return`

    async def count_tokens(self, messages: list[Message]) -> int:
        del messages
        return 1


async def test_fallback_client_chat_falls_through_on_failure() -> None:
    client = FallbackLLMClient(
        (_ScriptedClient("first", fails=True), _ScriptedClient("second", fails=False))
    )
    response = await client.chat([Message(role=MessageRole.USER, content="hi")])
    assert response.text == "answer from second"


async def test_fallback_client_chat_raises_when_every_entry_fails() -> None:
    client = FallbackLLMClient(
        (_ScriptedClient("first", fails=True), _ScriptedClient("second", fails=True))
    )
    messages = [Message(role=MessageRole.USER, content="hi")]
    with pytest.raises(RuntimeError, match="second is down"):
        await client.chat(messages)


async def test_fallback_client_identity_matches_first_entry() -> None:
    client = FallbackLLMClient(
        (_ScriptedClient("first", fails=True), _ScriptedClient("second", fails=False))
    )
    assert client.provider_id == "first"


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
        openrouter_api_key="k",
        local_llm_base_url="http://localhost:12434/engines",
    )
    build_client("anthropic", "claude-sonnet-5", settings)
    build_client("openai", "gpt-5.5", settings)
    build_client("gemini", "gemini-3.5-flash", settings)
    build_client("openrouter", "meta-llama/llama-3.3-70b-instruct", settings)
    build_client("local", "qwen3.6", settings)
