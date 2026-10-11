"""On the shared platform key a Free account may only pick the light models; paid plans and callers
running on their own key are unrestricted. Over HTTP, so the gate is proven where it is enforced."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from accounts.crypto import generate_key
from accounts.entitlements import FREE_PLATFORM_MODELS
from config.settings import Settings
from schemas.enums import Tier
from tests.api.conftest import build_test_client, sign_in

pytestmark = pytest.mark.governance

HEAVY = "anthropic/claude-opus-4-8"
LIGHT = "anthropic/claude-haiku-4-5-20251001"


@pytest.fixture
def keyed_client(settings: Settings, agent_answer: str) -> Iterator[TestClient]:
    with_keys = settings.model_copy(update={"secret_encryption_keys": SecretStr(generate_key())})
    with build_test_client(with_keys, agent_answer=agent_answer) as client:
        yield client


def _chat(client: TestClient, headers: dict[str, str], model: str | None):
    body: dict[str, object] = {"message": "hi"}
    if model is not None:
        body["model"] = model
    return client.post("/api/v1/agent/query", json=body, headers=headers)


def test_the_light_set_does_not_include_the_heavy_models() -> None:
    assert LIGHT in FREE_PLATFORM_MODELS
    assert HEAVY not in FREE_PLATFORM_MODELS


def test_a_free_account_cannot_pick_a_heavy_model_on_the_shared_key(
    client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    refused = _chat(client, headers, HEAVY)
    assert refused.status_code == 403
    assert refused.json()["code"] == "model_requires_upgrade"
    assert _chat(client, headers, LIGHT).status_code == 200
    assert _chat(client, headers, None).status_code == 200  # the platform default is always fine


def test_a_paid_account_can_pick_any_model(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.PREMIUM)
    assert _chat(client, headers, HEAVY).status_code == 200


def test_a_caller_on_their_own_key_is_not_restricted(
    keyed_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(keyed_client, settings, tier=Tier.FREE)
    saved = keyed_client.post(
        "/api/v1/me/keys", json={"provider": "anthropic", "key": "sk-own"}, headers=headers
    )
    assert saved.status_code == 204
    assert _chat(keyed_client, headers, HEAVY).status_code == 200


def test_the_entitlement_view_tells_the_picker_what_is_included(
    client: TestClient, settings: Settings
) -> None:
    free, _ = sign_in(client, settings, tier=Tier.FREE)
    pro, _ = sign_in(client, settings, tier=Tier.PRO)
    assert LIGHT in client.get("/api/v1/me", headers=free).json()["entitlements"]["platform_models"]
    assert client.get("/api/v1/me", headers=pro).json()["entitlements"]["platform_models"] is None
