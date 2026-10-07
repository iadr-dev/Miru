"""Bring-your-own-key end to end over HTTP: a saved key lifts the chat cap, is used for the whole
turn on the user's model, is required for the picked model's provider, and is never logged."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from accounts.crypto import generate_key
from config.settings import Settings
from llm import registry
from schemas.enums import Tier
from tests.api.conftest import build_test_client, sign_in
from tests.api.test_routes import _parse_sse

pytestmark = pytest.mark.governance

RAW_KEY = "sk-byok-plaintext-zzz"


@pytest.fixture
def byok_client(settings: Settings, agent_answer: str) -> Iterator[TestClient]:
    with_keys = settings.model_copy(update={"secret_encryption_keys": generate_key()})
    with build_test_client(with_keys, agent_answer=agent_answer) as client:
        yield client


def _save_key(client: TestClient, headers: dict[str, str], provider: str) -> None:
    body = {"provider": provider, "key": RAW_KEY}
    assert client.post("/api/v1/me/keys", json=body, headers=headers).status_code == 204


def _chat(client: TestClient, headers: dict[str, str], model: str | None = None):
    body: dict[str, object] = {"message": "hi"}
    if model is not None:
        body["model"] = model
    return client.post("/api/v1/agent/query", json=body, headers=headers)


def test_only_known_providers_can_be_saved(byok_client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(byok_client, settings, tier=Tier.FREE)
    resp = byok_client.post(
        "/api/v1/me/keys", json={"provider": "not-a-provider", "key": RAW_KEY}, headers=headers
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "unsupported_provider"


def test_saved_providers_are_listed_by_name_only(
    byok_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(byok_client, settings, tier=Tier.FREE)
    assert byok_client.get("/api/v1/me/keys", headers=headers).json() == {"providers": []}
    _save_key(byok_client, headers, "openai")
    _save_key(byok_client, headers, "anthropic")
    listed = byok_client.get("/api/v1/me/keys", headers=headers)
    assert listed.json() == {"providers": ["anthropic", "openai"]}
    assert RAW_KEY not in listed.text


def test_me_reports_byok_truthfully(byok_client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(byok_client, settings, tier=Tier.FREE)
    before = byok_client.get("/api/v1/me", headers=headers).json()["entitlements"]
    assert before["byok_active"] is False
    _save_key(byok_client, headers, "anthropic")
    after = byok_client.get("/api/v1/me", headers=headers).json()["entitlements"]
    assert after["byok_active"] is True
    assert after["chat_limit_per_day"] is None


def test_a_free_user_with_a_matching_key_is_not_capped(
    byok_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(byok_client, settings, tier=Tier.FREE)  # 3 chats/day without a key
    _save_key(byok_client, headers, "anthropic")  # the default model's provider
    for _ in range(5):
        assert _chat(byok_client, headers).status_code == 200


def test_the_turn_runs_in_a_byok_scope_on_the_users_model_and_key(
    byok_client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _ = sign_in(byok_client, settings, tier=Tier.FREE)
    _save_key(byok_client, headers, "openai")
    seen: list[registry.ByokOverride | None] = []
    orchestrator = byok_client.app.state.app_state.orchestrator  # type: ignore[attr-defined]
    original = orchestrator.run

    async def spy(*args, **kwargs):
        seen.append(registry._byok.get())
        return await original(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "run", spy)
    assert _chat(byok_client, headers, "openai/gpt-5.4-mini").status_code == 200

    assert len(seen) == 1
    active = seen[0]
    assert active is not None
    assert (active.provider_id, active.model_id) == ("openai", "gpt-5.4-mini")
    assert active.api_key == RAW_KEY
    assert registry._byok.get() is None


def test_a_model_from_a_provider_without_a_key_is_refused_not_served_on_ours(
    byok_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(byok_client, settings, tier=Tier.FREE)
    _save_key(byok_client, headers, "openai")
    refused = _chat(byok_client, headers, "anthropic/claude-sonnet-4-6")
    assert refused.status_code == 409
    assert refused.json()["code"] == "byok_key_required"
    assert "openai" in refused.json()["message"]
    assert RAW_KEY not in refused.text
    # ...and the default model (Anthropic) is refused the same way when no pick is made.
    assert _chat(byok_client, headers).status_code == 409


def test_removing_the_keys_restores_the_plans_normal_limits(
    byok_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(byok_client, settings, tier=Tier.FREE)
    _save_key(byok_client, headers, "anthropic")
    assert byok_client.delete("/api/v1/me/keys/anthropic", headers=headers).status_code == 204
    for _ in range(3):
        assert _chat(byok_client, headers).status_code == 200
    blocked = _chat(byok_client, headers)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "chat_limit_reached"


def test_the_key_is_never_logged_or_echoed(
    byok_client: TestClient, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    headers, _ = sign_in(byok_client, settings, tier=Tier.FREE)
    with caplog.at_level(logging.DEBUG):
        _save_key(byok_client, headers, "anthropic")
        resp = _chat(byok_client, headers)
    assert resp.status_code == 200
    assert _parse_sse(resp.text)[-1]["data"]["status"] == "done"
    assert RAW_KEY not in caplog.text
    assert RAW_KEY not in resp.text
