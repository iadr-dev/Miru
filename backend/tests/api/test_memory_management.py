"""Memory management over HTTP: search, edit in place, clear-all, the pause switch, and the
last-used stamp that recall leaves on the memories it puts in front of the model."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from schemas.enums import Tier
from tests.api.conftest import sign_in

pytestmark = pytest.mark.governance


def _save(client: TestClient, headers: dict[str, str], key: str, text: str, kind: str = "finding"):
    resp = client.post(
        "/api/v1/memory",
        json={"scope": "user", "type": kind, "key": key, "content": {"text": text}},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_search_keeps_only_memories_that_mention_the_query(
    client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    _save(client, headers, "etf", "我關注高股息 ETF")
    _save(client, headers, "semis", "我關注半導體")
    found = client.get("/api/v1/memory", params={"q": "ETF"}, headers=headers).json()["memories"]
    assert [m["key"] for m in found] == ["etf"]
    assert len(client.get("/api/v1/memory", headers=headers).json()["memories"]) == 2


def test_edit_changes_the_content_and_keeps_the_identity(
    client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    memory = _save(client, headers, "horizon", "短線")
    resp = client.patch(
        f"/api/v1/memory/{memory['id']}", json={"content": {"text": "長期"}}, headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == memory["id"]
    assert body["key"] == "horizon"
    assert body["content"] == {"text": "長期"}


def test_edit_is_governed_and_owner_scoped(client: TestClient, settings: Settings) -> None:
    owner, _ = sign_in(client, settings, tier=Tier.FREE)
    other, _ = sign_in(client, settings, tier=Tier.FREE)
    memory = _save(client, owner, "note", "原本內容")
    rejected = client.patch(
        f"/api/v1/memory/{memory['id']}",
        json={"content": {"text": "這檔股票目標價 1200"}},
        headers=owner,
    )
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "memory_rejected"
    stolen = client.patch(
        f"/api/v1/memory/{memory['id']}", json={"content": {"text": "x"}}, headers=other
    )
    assert stolen.status_code == 404


def test_clear_all_forgets_everything_for_that_user_only(
    client: TestClient, settings: Settings
) -> None:
    mine, _ = sign_in(client, settings, tier=Tier.FREE)
    theirs, _ = sign_in(client, settings, tier=Tier.FREE)
    _save(client, mine, "a", "一")
    _save(client, mine, "b", "二")
    _save(client, theirs, "c", "三")
    assert client.delete("/api/v1/memory", headers=mine).json() == {"deleted": 2}
    assert client.get("/api/v1/memory", headers=mine).json()["memories"] == []
    assert len(client.get("/api/v1/memory", headers=theirs).json()["memories"]) == 1


def test_the_pause_switch_defaults_on_and_persists(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    assert client.get("/api/v1/memory/settings", headers=headers).json() == {"enabled": True}
    off = client.put("/api/v1/memory/settings", json={"enabled": False}, headers=headers)
    assert off.json() == {"enabled": False}
    assert client.get("/api/v1/memory/settings", headers=headers).json() == {"enabled": False}
    # pausing keeps what is stored
    _save(client, headers, "kept", "仍然保留")
    assert len(client.get("/api/v1/memory", headers=headers).json()["memories"]) == 1
