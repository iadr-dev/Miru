"""BYOK scope: inside it every role resolves to the user's own model and key; outside it, never."""

from __future__ import annotations

import asyncio

import pytest

from config.settings import Settings
from llm import registry
from llm.base import LLMResponse, ModelCapabilities, StopReason, TextDeltaEvent, TokenUsage
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
        self.capabilities = ModelCapabilities()


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
    return ByokOverride(
        provider_id="openai", api_key=SECRET, main_model="gpt-5.4-mini", cheap_model="gpt-4o-mini"
    )


def test_every_role_uses_the_users_key_and_nothing_else(built: list[_Built]) -> None:
    settings = Settings()
    with byok_scope(_override()):
        for role in ModelRole:
            client = resolve_role(role, settings)
            assert client.provider_id == "openai"
            assert client.model_id == (
                "gpt-4o-mini" if role is ModelRole.CLASSIFIER else "gpt-5.4-mini"
            )
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
        with byok_scope(ByokOverride("openai", key, "m", "m")):
            await asyncio.sleep(0)  # let the other turn run while this scope is open
            client = resolve_role(ModelRole.AGENT, Settings())
            await asyncio.sleep(0)
            return client.key  # type: ignore[attr-defined,no-any-return]

    assert await asyncio.gather(turn("key-a"), turn("key-b")) == ["key-a", "key-b"]


def test_the_key_never_appears_in_the_repr() -> None:
    assert SECRET not in repr(_override())


def test_classifier_gets_the_cheap_model_and_the_rest_the_main(built: list[_Built]) -> None:
    override = ByokOverride("anthropic", SECRET, "claude-sonnet-5-5", "claude-haiku-5-5")
    with byok_scope(override):
        by_role = {role: resolve_role(role, Settings()) for role in ModelRole}
    assert by_role[ModelRole.CLASSIFIER].model_id == "claude-haiku-5-5"
    for role in (ModelRole.AGENT, ModelRole.SYNTHESIS, ModelRole.SUBAGENT):
        assert by_role[role].model_id == "claude-sonnet-5-5"
    with byok_scope(override):
        picked = resolve_role_with_override(ModelRole.CLASSIFIER, "openai/gpt-5.4", Settings())
        assert picked.model_id == "claude-haiku-5-5"
        assert resolve_role_with_override(ModelRole.AGENT, None, Settings()).model_id == (
            "claude-sonnet-5-5"
        )


def test_a_cheap_model_equal_to_main_is_a_single_client(built: list[_Built]) -> None:
    with byok_scope(ByokOverride("openrouter", SECRET, "openrouter/auto", "openrouter/auto")):
        client = resolve_role(ModelRole.CLASSIFIER, Settings())
    assert isinstance(client, _Built)
    assert len(built) == 1


class _ModelRejectedError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("model rejected")
        self.status_code = status_code


class _Recording:
    """A client that fails with `fail` (if set) and otherwise answers with its model id."""

    def __init__(self, provider: str, model: str, key: str | None, fail: Exception | None) -> None:
        self.provider_id = provider
        self.model_id = model
        self.key = key
        self.capabilities = ModelCapabilities()
        self.fail = fail

    async def chat(self, messages, tools=None, *, native_web_search=False):
        if self.fail is not None:
            raise self.fail
        return LLMResponse(
            text=self.model_id,
            tool_calls=(),
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )

    async def stream(self, messages, tools=None, *, native_web_search=False):
        response = await self.chat(messages, tools)
        yield TextDeltaEvent(text=response.text or "")

    async def count_tokens(self, messages) -> int:
        return 1


def _rejecting_cheap(monkeypatch: pytest.MonkeyPatch, status: int) -> list[tuple[str, str | None]]:
    calls: list[tuple[str, str | None]] = []

    def fake(provider, model, settings=None, *, api_key=None):
        calls.append((model, api_key))
        fail = _ModelRejectedError(status) if model == "cheap-m" else None
        return _Recording(provider, model, api_key, fail)

    monkeypatch.setattr(registry, "build_client", fake)
    return calls


async def test_a_rejected_cheap_model_degrades_to_main_on_the_same_user_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _rejecting_cheap(monkeypatch, 404)
    with byok_scope(ByokOverride("openai", SECRET, "main-m", "cheap-m")):
        client = resolve_role(ModelRole.CLASSIFIER, Settings(anthropic_api_key="platform-key"))
        response = await client.chat([])
        events = [event async for event in client.stream([])]
    assert response.text == "main-m"
    assert [getattr(e, "text", None) for e in events] == ["main-m"]
    assert {key for _, key in calls} == {SECRET}  # never a platform key


async def test_a_non_model_error_on_the_cheap_model_is_not_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _rejecting_cheap(monkeypatch, 401)  # a bad key is not a model rejection
    with byok_scope(ByokOverride("openai", SECRET, "main-m", "cheap-m")):
        client = resolve_role(ModelRole.CLASSIFIER, Settings())
        with pytest.raises(_ModelRejectedError):
            await client.chat([])


def test_a_cheap_client_that_cannot_be_built_degrades_to_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(provider, model, settings=None, *, api_key=None):
        if model == "cheap-m":
            raise ValueError("unsupported")
        return _Recording(provider, model, api_key, None)

    monkeypatch.setattr(registry, "build_client", fake)
    with byok_scope(ByokOverride("openai", SECRET, "main-m", "cheap-m")):
        assert resolve_role(ModelRole.CLASSIFIER, Settings()).model_id == "main-m"


def test_assert_no_byok_scope_passes_outside_and_raises_inside() -> None:
    registry.assert_no_byok_scope()
    with byok_scope(_override()), pytest.raises(RuntimeError, match="BYOK scope"):
        registry.assert_no_byok_scope()
    registry.assert_no_byok_scope()  # the scope has ended


def test_the_platform_only_jobs_refuse_to_start_inside_a_byok_scope() -> None:
    import datetime as dt  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from analytics.supply_chain.verification import job  # noqa: PLC0415
    from analytics.themes import __main__ as themes_main  # noqa: PLC0415

    settings = Settings()
    with byok_scope(_override()):
        with pytest.raises(RuntimeError, match="BYOK scope"):
            asyncio.run(
                job.execute_research(
                    settings,
                    None,  # type: ignore[arg-type]
                    today=dt.date(2026, 10, 19),
                    source="all",
                    limit=None,
                    dry_run=True,
                    is_leader=lambda: True,
                    root=Path(),
                )
            )
        with pytest.raises(RuntimeError, match="BYOK scope"):
            asyncio.run(
                job.run_research_job(
                    dt.date(2026, 10, 19),
                    None,  # type: ignore[arg-type]
                    settings=settings,
                )
            )
        with pytest.raises(RuntimeError, match="BYOK scope"):
            asyncio.run(themes_main.main())


def test_agent_model_spec_prefers_the_pick_then_the_role_default() -> None:
    gemini = registry.agent_model_spec("gemini/gemini-3.5-flash", Settings())
    assert gemini == ("gemini", "gemini-3.5-flash")
    routed = registry.agent_model_spec("openrouter/openrouter/auto", Settings())
    assert routed == ("openrouter", "openrouter/auto")
    provider, _ = registry.agent_model_spec(None, Settings(model_role_agent=None))
    assert provider == "anthropic"
