"""A chat can pin its own 語氣 (or go back to following the account setting), and every answer
records which preset shaped it."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from schemas.enums import Tier
from tests.api.conftest import sign_in
from tests.api.test_routes import _parse_sse

pytestmark = pytest.mark.governance


def _new_thread(client: TestClient, headers: dict[str, str]) -> str:
    resp = client.post("/api/v1/threads", json={}, headers=headers)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _patch(client: TestClient, headers: dict[str, str], thread_id: str, **body: object):
    return client.patch(f"/api/v1/threads/{thread_id}", json=body, headers=headers)


def test_a_chat_can_pin_a_preset_and_return_to_the_account_setting(
    client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    thread = _new_thread(client, headers)
    pinned = _patch(client, headers, thread, personality_override="data")
    assert pinned.status_code == 200
    assert pinned.json()["personality_override"] == "data"
    cleared = _patch(client, headers, thread, personality_override="default")
    assert cleared.json()["personality_override"] is None


def test_an_unknown_preset_is_refused_and_not_stored(
    client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    thread = _new_thread(client, headers)
    bad = _patch(client, headers, thread, personality_override="nope")
    assert bad.status_code == 422
    assert bad.json()["code"] == "unknown_personality"
    assert (
        client.get(f"/api/v1/threads/{thread}", headers=headers).json()["personality_override"]
        is None
    )


def test_the_choice_can_ride_with_the_first_message(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    resp = client.post(
        "/api/v1/agent/query", json={"message": "hi", "personality": "concise"}, headers=headers
    )
    assert resp.status_code == 200
    frames = _parse_sse(resp.text)
    thread_id = next(f for f in frames if f["event"] == "turn.start")["data"]["thread_id"]  # type: ignore[index]
    thread = client.get(f"/api/v1/threads/{thread_id}", headers=headers).json()
    assert thread["personality_override"] == "concise"
    refused = client.post(
        "/api/v1/agent/query", json={"message": "hi", "personality": "nope"}, headers=headers
    )
    assert refused.status_code == 422


def test_each_answer_says_which_preset_shaped_it(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    # nothing chosen: the default
    frames = _parse_sse(
        client.post("/api/v1/agent/query", json={"message": "hi"}, headers=headers).text
    )
    used = [f for f in frames if f["event"] == "personality.used"]
    assert [u["data"]["personality"] for u in used] == ["steady"]  # type: ignore[index]
    # the account setting
    client.patch("/api/v1/me", json={"personality_id": "coach"}, headers=headers)
    frames = _parse_sse(
        client.post("/api/v1/agent/query", json={"message": "hi"}, headers=headers).text
    )
    assert [f["data"]["personality"] for f in frames if f["event"] == "personality.used"] == [
        "coach"
    ]  # type: ignore[index]
    # a chat's own choice beats the account setting, and it is stored with the answer
    frames = _parse_sse(
        client.post(
            "/api/v1/agent/query",
            json={"message": "hi", "personality": "data"},
            headers=headers,
        ).text
    )
    assert [f["data"]["personality"] for f in frames if f["event"] == "personality.used"] == [
        "data"
    ]  # type: ignore[index]
    thread_id = next(f for f in frames if f["event"] == "turn.start")["data"]["thread_id"]  # type: ignore[index]
    thread = client.get(f"/api/v1/threads/{thread_id}", headers=headers).json()
    usages = [t["usage"] for t in thread["turns"] if t["role"] == "assistant" and t["usage"]]
    assert usages[-1]["personality"] == "data"
