"""The pet in a real turn: its settings, its reward events, its tone hint, and the login streak."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from pets import config
from schemas.enums import Tier
from tests.api.conftest import sign_in
from tests.api.test_routes import _parse_sse

pytestmark = pytest.mark.governance


def _chat(client: TestClient, headers: dict[str, str]) -> list[dict[str, object]]:
    resp = client.post("/api/v1/agent/query", json={"message": "hi"}, headers=headers)
    assert resp.status_code == 200
    return _parse_sse(resp.text)


def test_pet_replies_are_off_until_the_user_opts_in(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    assert client.get("/api/v1/pets/settings", headers=headers).json() == {
        "hint_enabled": False,
        "companion_visible": True,
    }
    on = client.patch("/api/v1/pets/settings", json={"hint_enabled": True}, headers=headers)
    assert on.json() == {"hint_enabled": True, "companion_visible": True}
    hidden = client.patch(
        "/api/v1/pets/settings", json={"companion_visible": False}, headers=headers
    )
    assert hidden.json() == {"hint_enabled": True, "companion_visible": False}


def test_a_chat_levels_the_equipped_pet_and_says_so(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.PRO)
    assert client.post("/api/v1/pets/pull", headers=headers).status_code == 201
    levelled: list[dict[str, object]] = []
    for _ in range(config.XP_PER_LEVEL // config.XP_PER_CHAT):
        levelled += [f for f in _chat(client, headers) if f["event"] == "pet.leveled"]
    assert [f["data"]["level"] for f in levelled] == [2]  # type: ignore[index]
    active = client.get("/api/v1/pets/active", headers=headers).json()["active"]
    assert active["level"] == 2


def test_chats_past_the_daily_cap_earn_nothing(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.PRO)
    client.post("/api/v1/pets/pull", headers=headers)
    for _ in range(config.DAILY_CHAT_XP_CAP // config.XP_PER_CHAT + 2):
        _chat(client, headers)
    active = client.get("/api/v1/pets/active", headers=headers).json()["active"]
    assert (active["level"], active["xp"]) == (2, 0)


def test_the_first_page_load_of_a_day_starts_the_streak(
    client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    client.get("/api/v1/me", headers=headers)
    client.get("/api/v1/me", headers=headers)
    progress = client.get("/api/v1/pets/progress", headers=headers).json()
    assert progress["streak_days"] == 1
    assert progress["daily_chat_xp_cap"] == config.DAILY_CHAT_XP_CAP
