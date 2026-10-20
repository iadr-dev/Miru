"""The model catalog (`GET /models`), per-request / per-thread override validation, clearing an
override, and the saved default model. Over HTTP, so each rule is proven where it is enforced.

Only a BYOK caller (a saved key) can choose a model; for everyone else the platform profile decides
and any override is ignored (not stored, not a 403)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from accounts.crypto import generate_key
from config.settings import Settings
from llm import registry
from schemas.enums import Tier
from tests.api.conftest import build_test_client, sign_in

pytestmark = pytest.mark.governance

SONNET = "anthropic/claude-sonnet-5-5"
OPUS = "anthropic/claude-opus-5-5"
GPT = "openai/gpt-5.4-mini"


@pytest.fixture
def keyed_client(settings: Settings, agent_answer: str) -> Iterator[TestClient]:
    with_keys = settings.model_copy(update={"secret_encryption_keys": SecretStr(generate_key())})
    with build_test_client(with_keys, agent_answer=agent_answer) as client:
        yield client


def _save_key(client: TestClient, headers: dict[str, str], provider: str) -> None:
    body = {"provider": provider, "key": "sk-own"}
    assert client.post("/api/v1/me/keys", json=body, headers=headers).status_code == 204


def _chat(
    client: TestClient, headers: dict[str, str], model: str | None, thread_id: str | None = None
):
    body: dict[str, object] = {"message": "hi"}
    if model is not None:
        body["model"] = model
    if thread_id is not None:
        body["thread_id"] = thread_id
    return client.post("/api/v1/agent/query", json=body, headers=headers)


def _new_thread(client: TestClient, headers: dict[str, str]) -> str:
    return client.post("/api/v1/threads", json={"title": "t"}, headers=headers).json()["id"]


def _patch(client: TestClient, headers: dict[str, str], thread_id: str, body: dict[str, object]):
    return client.patch(f"/api/v1/threads/{thread_id}", json=body, headers=headers)


def _override(client: TestClient, headers: dict[str, str], thread_id: str) -> str | None:
    return client.get(f"/api/v1/threads/{thread_id}", headers=headers).json()["model_override"]


# ---- GET /models ---------------------------------------------------------------------------


def test_a_caller_without_a_key_gets_no_catalog(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.PRO)
    resp = client.get("/api/v1/models", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"selectable": False, "models": []}


def test_models_requires_sign_in(client: TestClient) -> None:
    assert client.get("/api/v1/models").status_code == 401


def test_the_catalog_is_limited_to_providers_with_a_saved_key(
    keyed_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    _save_key(keyed_client, headers, "openai")
    body = keyed_client.get("/api/v1/models", headers=headers).json()
    assert body["selectable"] is True
    assert {m["provider"] for m in body["models"]} == {"openai"}
    assert GPT in {m["id"] for m in body["models"]}
    assert all(m["label_zh"] and m["label_en"] for m in body["models"])
    # the effective main (no saved default) is the provider's curated default, and only that one
    assert [m["id"] for m in body["models"] if m["is_default"]] == ["openai/gpt-5.6-sol"]

    _save_key(keyed_client, headers, "anthropic")
    both = keyed_client.get("/api/v1/models", headers=headers).json()["models"]
    assert {m["provider"] for m in both} == {"anthropic", "openai"}
    assert [m["id"] for m in both if m["is_default"]] == [SONNET]  # anthropic comes first


def test_removing_the_keys_empties_the_catalog_again(
    keyed_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    _save_key(keyed_client, headers, "anthropic")
    assert keyed_client.delete("/api/v1/me/keys/anthropic", headers=headers).status_code == 204
    assert keyed_client.get("/api/v1/models", headers=headers).json() == {
        "selectable": False,
        "models": [],
    }


# ---- per-request pick (POST /agent/query) --------------------------------------------------


def test_a_non_byok_pick_is_ignored_not_refused_or_stored(
    client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    for pick in (OPUS, "not-a-model", "nope/nothing"):
        assert _chat(client, headers, pick).status_code == 200  # no 403, no 422
    threads = client.get("/api/v1/threads", headers=headers).json()["threads"]
    assert threads
    assert all(t["model_override"] is None for t in threads)


def test_a_byok_pick_must_be_provider_slash_model(
    keyed_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    _save_key(keyed_client, headers, "anthropic")
    for bad in ("claude-sonnet-5-5", "anthropic/", "/claude", "bogus/model"):
        resp = _chat(keyed_client, headers, bad)
        assert resp.status_code == 422, bad
        assert resp.json()["code"] == "invalid_model"


def test_a_byok_pick_outside_the_catalog_is_a_calm_422(
    keyed_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    _save_key(keyed_client, headers, "anthropic")
    resp = _chat(keyed_client, headers, "anthropic/claude-opus-4-8")
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_model"
    assert "not in the model list" in resp.json()["message"]


def test_a_byok_pick_from_a_provider_without_a_key_is_the_409(
    keyed_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    _save_key(keyed_client, headers, "openai")
    resp = _chat(keyed_client, headers, SONNET)
    assert resp.status_code == 409
    assert resp.json()["code"] == "byok_key_required"


def test_a_valid_byok_pick_runs_and_is_stored_on_the_thread(
    keyed_client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    _save_key(keyed_client, headers, "anthropic")
    seen: list[registry.ByokOverride | None] = []
    orchestrator = keyed_client.app.state.app_state.orchestrator  # type: ignore[attr-defined]
    original = orchestrator.run

    async def spy(*args, **kwargs):
        seen.append(registry._byok.get())
        return await original(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "run", spy)
    assert _chat(keyed_client, headers, OPUS).status_code == 200
    assert seen[0] is not None
    assert seen[0].main_model == "claude-opus-5-5"
    threads = keyed_client.get("/api/v1/threads", headers=headers).json()["threads"]
    assert [t["model_override"] for t in threads] == [OPUS]


def test_a_stored_override_is_not_used_once_the_keys_are_gone(
    keyed_client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    _save_key(keyed_client, headers, "anthropic")
    thread_id = _new_thread(keyed_client, headers)
    assert _patch(keyed_client, headers, thread_id, {"model_override": OPUS}).status_code == 200
    assert keyed_client.delete("/api/v1/me/keys/anthropic", headers=headers).status_code == 204

    seen: list[registry.ByokOverride | None] = []
    orchestrator = keyed_client.app.state.app_state.orchestrator  # type: ignore[attr-defined]
    original = orchestrator.run

    async def spy(*args, **kwargs):
        seen.append(registry._byok.get())
        assert kwargs.get("model_override") is None
        return await original(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "run", spy)
    assert _chat(keyed_client, headers, None, thread_id).status_code == 200
    assert seen == [None]


# ---- PATCH /threads model_override ---------------------------------------------------------


def test_patch_ignores_a_non_byok_override(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.PRO)
    thread_id = _new_thread(client, headers)
    resp = _patch(client, headers, thread_id, {"model_override": OPUS})
    assert resp.status_code == 200
    assert resp.json()["model_override"] is None


def test_patch_validates_a_byok_override(keyed_client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    _save_key(keyed_client, headers, "anthropic")
    thread_id = _new_thread(keyed_client, headers)
    assert _patch(keyed_client, headers, thread_id, {"model_override": "x"}).status_code == 422
    unknown = _patch(keyed_client, headers, thread_id, {"model_override": "anthropic/nope"})
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "invalid_model"
    no_key = _patch(keyed_client, headers, thread_id, {"model_override": GPT})
    assert no_key.status_code == 409
    assert no_key.json()["code"] == "byok_key_required"
    assert _override(keyed_client, headers, thread_id) is None  # nothing was stored


def test_patch_sets_keeps_and_clears_the_override(
    keyed_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    _save_key(keyed_client, headers, "anthropic")
    thread_id = _new_thread(keyed_client, headers)

    assert _patch(keyed_client, headers, thread_id, {"model_override": OPUS}).status_code == 200
    assert _override(keyed_client, headers, thread_id) == OPUS

    # omitting the field leaves it alone
    assert _patch(keyed_client, headers, thread_id, {"title": "renamed"}).status_code == 200
    assert _override(keyed_client, headers, thread_id) == OPUS

    # null clears
    assert _patch(keyed_client, headers, thread_id, {"model_override": None}).status_code == 200
    assert _override(keyed_client, headers, thread_id) is None

    # "" clears too
    _patch(keyed_client, headers, thread_id, {"model_override": SONNET})
    assert _patch(keyed_client, headers, thread_id, {"model_override": ""}).status_code == 200
    assert _override(keyed_client, headers, thread_id) is None


def test_a_non_byok_caller_can_still_clear_a_stale_override(
    keyed_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    _save_key(keyed_client, headers, "anthropic")
    thread_id = _new_thread(keyed_client, headers)
    _patch(keyed_client, headers, thread_id, {"model_override": OPUS})
    assert keyed_client.delete("/api/v1/me/keys/anthropic", headers=headers).status_code == 204
    assert _patch(keyed_client, headers, thread_id, {"model_override": None}).status_code == 200
    assert _override(keyed_client, headers, thread_id) is None


# ---- saved default model -------------------------------------------------------------------


def test_default_model_endpoints_need_a_key_to_set(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.PRO)
    assert client.get("/api/v1/me/model-default", headers=headers).json() == {"default_model": None}
    refused = client.put(
        "/api/v1/me/model-default", json={"default_model": SONNET}, headers=headers
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "byok_required"
    cleared = client.put("/api/v1/me/model-default", json={"default_model": None}, headers=headers)
    assert cleared.status_code == 200


def test_default_model_is_validated_saved_and_cleared(
    keyed_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    _save_key(keyed_client, headers, "anthropic")
    url = "/api/v1/me/model-default"

    assert keyed_client.put(url, json={"default_model": "junk"}, headers=headers).status_code == 422
    unknown = keyed_client.put(url, json={"default_model": "anthropic/nope"}, headers=headers)
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "invalid_model"
    no_key = keyed_client.put(url, json={"default_model": GPT}, headers=headers)
    assert no_key.status_code == 409
    assert no_key.json()["code"] == "byok_key_required"

    saved = keyed_client.put(url, json={"default_model": OPUS}, headers=headers)
    assert saved.status_code == 200
    assert saved.json() == {"default_model": OPUS}
    assert keyed_client.get(url, headers=headers).json() == {"default_model": OPUS}
    assert keyed_client.get("/api/v1/me", headers=headers).json()["user"]["default_model"] == OPUS
    models = keyed_client.get("/api/v1/models", headers=headers).json()["models"]
    assert [m["id"] for m in models if m["is_default"]] == [OPUS]

    assert keyed_client.put(url, json={"default_model": ""}, headers=headers).status_code == 200
    assert keyed_client.get(url, headers=headers).json() == {"default_model": None}


def test_a_chat_with_no_pick_runs_on_the_saved_default(
    keyed_client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    _save_key(keyed_client, headers, "anthropic")
    _save_key(keyed_client, headers, "openai")
    put = keyed_client.put("/api/v1/me/model-default", json={"default_model": GPT}, headers=headers)
    assert put.status_code == 200
    seen: list[registry.ByokOverride | None] = []
    orchestrator = keyed_client.app.state.app_state.orchestrator  # type: ignore[attr-defined]
    original = orchestrator.run

    async def spy(*args, **kwargs):
        seen.append(registry._byok.get())
        return await original(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "run", spy)
    assert _chat(keyed_client, headers, None).status_code == 200
    assert seen[0] is not None
    assert (seen[0].provider_id, seen[0].main_model) == ("openai", "gpt-5.4-mini")

    # drop the openai key: the saved default no longer applies, the first keyed provider wins
    assert keyed_client.delete("/api/v1/me/keys/openai", headers=headers).status_code == 204
    assert _chat(keyed_client, headers, None).status_code == 200
    assert seen[1] is not None
    assert (seen[1].provider_id, seen[1].main_model) == ("anthropic", "claude-sonnet-5-5")


def test_the_entitlement_view_no_longer_lists_platform_models(
    client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    entitlements = client.get("/api/v1/me", headers=headers).json()["entitlements"]
    assert "platform_models" not in entitlements
