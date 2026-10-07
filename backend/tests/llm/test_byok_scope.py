"""BYOK scope: inside it every role resolves to the user's own model and key; outside it, never."""

from __future__ import annotations

import asyncio

import pytest

from config.settings import Settings
from llm import registry
from llm.registry import (
    ByokOverride,
    ModelRole,
    byok_scope,
    resolve_role,
    resolve_role_with_override,
)

pytestmark = pytest.mark.governance

SECRET = "sk-user-own-key-123"


class _Built:
    def __init__(self, provider: str, model: str, key: str | None) -> None:
        self.provider_id = provider
        self.model_id = model
        self.key = key


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[_Built]:
    calls: list[_Built] = []

    def fake(
        provider: str,
        model: str,
        settings: Settings | None = None,
        *,
        api_key: str | None = None,
    ):
        client = _Built(provider, model, api_key)
        calls.append(client)
        return client

    monkeypatch.setattr(registry, "build_client", fake)
    return calls


def _override() -> ByokOverride:
    return ByokOverride(provider_id="openai", model_id="gpt-5.4-mini", api_key=SECRET)


def test_every_role_uses_the_users_model_and_key_and_nothing_else(built: list[_Built]) -> None:
    settings = Settings()
    with byok_scope(_override()):
        for role in ModelRole:
            client = resolve_role(role, settings)
            assert client.provider_id == "openai"
            assert client.model_id == "gpt-5.4-mini"
            assert client.key == SECRET  # type: ignore[attr-defined]
        # Even an explicit thread pick for another provider cannot escape the user's key.
        picked = resolve_role_with_override(ModelRole.AGENT, "anthropic/claude-opus-4-8", settings)
        assert picked.provider_id == "openai"
    assert all(call.key == SECRET for call in built)
    assert {call.provider_id for call in built} == {"openai"}  # no platform client was built


def test_outside_the_scope_nothing_is_overridden(built: list[_Built]) -> None:
    with byok_scope(_override()):
        pass
    resolve_role(ModelRole.SUBAGENT, Settings(anthropic_api_key="platform-key"))
    assert all(call.key != SECRET for call in built)


async def test_scopes_do_not_leak_between_concurrent_turns(built: list[_Built]) -> None:
    async def turn(key: str) -> str | None:
        with byok_scope(ByokOverride("openai", "m", key)):
            await asyncio.sleep(0)  # let the other turn run while this scope is open
            client = resolve_role(ModelRole.AGENT, Settings())
            await asyncio.sleep(0)
            return client.key  # type: ignore[attr-defined,no-any-return]

    assert await asyncio.gather(turn("key-a"), turn("key-b")) == ["key-a", "key-b"]


def test_the_key_never_appears_in_the_repr() -> None:
    assert SECRET not in repr(_override())


def test_agent_model_spec_prefers_the_pick_then_the_role_default() -> None:
    gemini = registry.agent_model_spec("gemini/gemini-3.5-flash", Settings())
    assert gemini == ("gemini", "gemini-3.5-flash")
    routed = registry.agent_model_spec("openrouter/openrouter/auto", Settings())
    assert routed == ("openrouter", "openrouter/auto")
    provider, _ = registry.agent_model_spec(None, Settings(model_role_agent=None))
    assert provider == "anthropic"
