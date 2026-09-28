"""HTTP tests for the Phase-11 routers: turn-lifecycle actions, thread management, voice,
market REST, pets, memory + watchlists, and accounts extras. Driven through `TestClient` against
the fully-wired app (stub LLM/providers, no network), matching `tests/api/test_routes.py`.
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from config.settings import Settings
from pets import config as pet_config
from schemas.enums import Tier
from tests.api.conftest import sign_in


def _create_thread(client: TestClient, headers: dict[str, str], title: str = "t") -> str:
    resp = client.post("/api/v1/threads", json={"title": title}, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


class TestThreadManagement:
    def test_patch_rename_star_archive_tag(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        thread_id = _create_thread(client, headers)
        resp = client.patch(
            f"/api/v1/threads/{thread_id}",
            json={"title": "Renamed", "starred": True, "archived": True, "tags": ["a", "b"]},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Renamed"
        assert body["starred"] is True
        assert body["archived"] is True
        assert list(body["tags"]) == ["a", "b"]
        assert body["version"] == 1

    def test_patch_model_override_round_trips(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        thread_id = _create_thread(client, headers)
        resp = client.patch(
            f"/api/v1/threads/{thread_id}",
            json={"model_override": "anthropic/claude-opus-4-8"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["model_override"] == "anthropic/claude-opus-4-8"

        get_resp = client.get(f"/api/v1/threads/{thread_id}", headers=headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["model_override"] == "anthropic/claude-opus-4-8"

    def test_patch_if_match_mismatch_409(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        thread_id = _create_thread(client, headers)
        resp = client.patch(
            f"/api/v1/threads/{thread_id}",
            json={"title": "x"},
            headers={**headers, "If-Match": "99"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "stale_version"

    def test_fork(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        thread_id = _create_thread(client, headers)
        resp = client.post(
            f"/api/v1/threads/{thread_id}/fork",
            json={"from_turn_id": "any", "title": "Forked"},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["id"] != thread_id

    def test_search_finds_by_title(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        _create_thread(client, headers, title="台積電 analysis")
        resp = client.get("/api/v1/threads/search", params={"q": "台積電"}, headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()["threads"]) == 1

    def test_export_json_and_md(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        thread_id = _create_thread(client, headers, title="Export me")
        j = client.get(f"/api/v1/threads/{thread_id}/export", headers=headers)
        assert j.status_code == 200
        assert j.json()["title"] == "Export me"
        md = client.get(
            f"/api/v1/threads/{thread_id}/export", params={"format": "md"}, headers=headers
        )
        assert md.status_code == 200
        assert "# Export me" in md.text

    def test_switch_branch_flips_leaf(
        self, client: TestClient, signed_in_user, agent_answer: str
    ) -> None:
        headers, _ = signed_in_user
        # Run a turn so there are real messages to branch to.
        client.post("/api/v1/agent/query", json={"message": "hi"}, headers=headers)
        thread_id = client.get("/api/v1/threads", headers=headers).json()["threads"][0]["id"]
        full = client.get(f"/api/v1/threads/{thread_id}", headers=headers).json()
        target_message_id = full["turns"][0]["messages"][0]["id"]
        resp = client.post(
            f"/api/v1/threads/{thread_id}/switch-branch",
            json={"target_message_id": target_message_id},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["active_leaf_message_id"] == target_message_id


class TestThreadIsolation:
    def test_user_b_cannot_patch_user_a_thread(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers_a, _ = sign_in(client, settings, tier=Tier.FREE)
        headers_b, _ = sign_in(client, settings, tier=Tier.FREE)
        thread_id = _create_thread(client, headers_a)
        resp = client.patch(
            f"/api/v1/threads/{thread_id}", json={"title": "hijack"}, headers=headers_b
        )
        assert resp.status_code == 404

    def test_user_b_cannot_cancel_unknown_turn(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers_a, _ = sign_in(client, settings, tier=Tier.FREE)
        headers_b, _ = sign_in(client, settings, tier=Tier.FREE)
        thread_id = _create_thread(client, headers_a)
        resp = client.post(f"/api/v1/threads/{thread_id}/turns/nope/cancel", headers=headers_b)
        assert resp.status_code == 404


class TestTurnLifecycle:
    def test_steer_without_running_turn_409(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        thread_id = _create_thread(client, headers)
        resp = client.post(
            f"/api/v1/threads/{thread_id}/steer",
            json={"message": "focus on dividends"},
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "no_running_turn"

    def test_cancel_unknown_turn_404(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        thread_id = _create_thread(client, headers)
        resp = client.post(f"/api/v1/threads/{thread_id}/turns/missing/cancel", headers=headers)
        assert resp.status_code == 404

    def test_stream_no_live_turn_returns_empty(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        thread_id = _create_thread(client, headers)
        resp = client.get(f"/api/v1/threads/{thread_id}/stream", headers=headers)
        assert resp.status_code == 200
        assert resp.text == ""

    # `feedback`/`regenerate`/`edit` are real, persisted endpoints now — see
    # `tests/api/test_turn_actions.py::TestTurnFeedback`/`TestRegenerateTurn`/`TestEditTurn`
    # for their actual coverage; this class no longer asserts a 501 stub.


class TestVoice:
    def test_transcribe_free_ok(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        audio = base64.b64encode("台積電最近如何".encode()).decode("ascii")
        resp = client.post(
            "/api/v1/voice/transcribe",
            json={"audio_base64": audio, "language": "zh-TW"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["text"] == "台積電最近如何"

    def test_speak_free_ok(self, client: TestClient, signed_in_user) -> None:
        """TTS read-aloud is Free, quota-bounded like STT — voice conversation mode is a
        Free-tier feature end to end, not gated behind Premium/Pro."""
        headers, _ = signed_in_user  # Free tier
        resp = client.post("/api/v1/voice/speak", json={"text": "測試"}, headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["content_type"]
        # The spoken text is caveat-guaranteed by the service.
        assert body["spoken_text"]

    def test_speak_premium_ok(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PREMIUM)
        resp = client.post("/api/v1/voice/speak", json={"text": "台積電營運穩健"}, headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["content_type"]
        # The spoken text is caveat-guaranteed by the service.
        assert body["spoken_text"]

    def test_transcribe_requires_signin(self, client: TestClient) -> None:
        assert (
            client.post("/api/v1/voice/transcribe", json={"audio_base64": "AA=="}).status_code
            == 401
        )


class TestPets:
    def test_catalog_and_progress(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        cat = client.get("/api/v1/pets/catalog")
        assert cat.status_code == 200
        assert len(cat.json()["catalog"]) > 0

        prog = client.get("/api/v1/pets/progress", headers=headers)
        assert prog.status_code == 200
        body = prog.json()
        assert body["available_pulls"] == 1  # free register pull
        # The response genuinely reports the configured pity thresholds + next-milestone
        # context the doc claims, not just the raw `pity_counter` — real values from
        # `pets.config`, not placeholders.
        assert body["pity_rare_at"] == pet_config.PITY_RARE_AT
        assert body["pity_legendary_at"] == pet_config.PITY_LEGENDARY_AT
        assert {int(k): v for k, v in body["level_milestones"].items()} == (
            pet_config.LEVEL_MILESTONE_PULLS
        )

    def test_pull_then_no_pulls_left(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        first = client.post("/api/v1/pets/pull", headers=headers)
        assert first.status_code == 201
        second = client.post("/api/v1/pets/pull", headers=headers)
        assert second.status_code == 409
        assert second.json()["code"] == "no_pulls_available"

    def test_equip_missing_404(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        resp = client.put("/api/v1/pets/nope/equip", headers=headers)
        assert resp.status_code == 404


class TestMemory:
    def test_upsert_list_delete(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        up = client.post(
            "/api/v1/memory",
            json={"type": "preference", "key": "risk_style", "content": {"style": "cautious"}},
            headers=headers,
        )
        assert up.status_code == 201
        memory_id = up.json()["id"]

        listed = client.get("/api/v1/memory", headers=headers).json()["memories"]
        assert any(m["id"] == memory_id for m in listed)

        assert client.delete(f"/api/v1/memory/{memory_id}", headers=headers).status_code == 204
        assert client.delete(f"/api/v1/memory/{memory_id}", headers=headers).status_code == 404

    def test_upsert_watchlist_type_rejected(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        resp = client.post(
            "/api/v1/memory",
            json={"type": "watchlist", "key": "k", "content": {}},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_trading_language_rejected(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        resp = client.post(
            "/api/v1/memory",
            json={"type": "finding", "key": "k", "content": {"note": "you should buy 2330"}},
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "memory_rejected"


class TestWatchlists:
    def test_create_add_list_remove(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        group = client.post(
            "/api/v1/watchlists", json={"name": "Core", "market": "TW"}, headers=headers
        )
        assert group.status_code == 201
        gid = group.json()["id"]

        add = client.put(f"/api/v1/watchlists/{gid}/items/2330", json={}, headers=headers)
        assert add.status_code == 200
        # Idempotent re-add returns the same row.
        again = client.put(f"/api/v1/watchlists/{gid}/items/2330", json={}, headers=headers)
        assert again.json()["id"] == add.json()["id"]

        # `market=TW` also covers the (TW, etf) asset class — and `GET /watchlists` seeds starter
        # presets for any (market, asset_class) combo this user has zero groups in yet (see
        # `WatchlistStore.seed_default_presets`), so a fresh test user's `(TW, etf)` groups get
        # seeded here too. Find this test's own "Core" group by id rather than assuming index 0.
        listed = client.get("/api/v1/watchlists", params={"market": "TW"}, headers=headers).json()
        own_view = next(view for view in listed["watchlists"] if view["group"]["id"] == gid)
        assert len(own_view["items"]) == 1

        rm = client.delete(f"/api/v1/watchlists/{gid}/items/2330", headers=headers)
        assert rm.status_code == 204
        # Idempotent remove.
        assert (
            client.delete(f"/api/v1/watchlists/{gid}/items/2330", headers=headers).status_code
            == 204
        )

    def test_patch_if_match_mismatch_409(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        gid = client.post(
            "/api/v1/watchlists", json={"name": "G", "market": "TW"}, headers=headers
        ).json()["id"]
        resp = client.patch(
            f"/api/v1/watchlists/{gid}",
            json={"name": "New"},
            headers={**headers, "If-Match": "42"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "stale_version"

    def test_patch_rename_bumps_version(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        gid = client.post(
            "/api/v1/watchlists", json={"name": "G", "market": "TW"}, headers=headers
        ).json()["id"]
        resp = client.patch(
            f"/api/v1/watchlists/{gid}",
            json={"name": "New"},
            headers={**headers, "If-Match": "0"},
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 1

    def test_isolation(self, client: TestClient, settings: Settings) -> None:
        headers_a, _ = sign_in(client, settings, tier=Tier.FREE)
        headers_b, _ = sign_in(client, settings, tier=Tier.FREE)
        gid = client.post(
            "/api/v1/watchlists", json={"name": "A", "market": "TW"}, headers=headers_a
        ).json()["id"]
        resp = client.delete(f"/api/v1/watchlists/{gid}", headers=headers_b)
        assert resp.status_code == 404


class TestAccountsExtra:
    def test_patch_me_profile(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        resp = client.patch(
            "/api/v1/me", json={"display_name": "New Name", "nationality": "TW"}, headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["display_name"] == "New Name"

    def test_patch_me_unknown_personality_422(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        resp = client.patch(
            "/api/v1/me", json={"personality_id": "does-not-exist"}, headers=headers
        )
        assert resp.status_code == 422

    def test_personality_presets_listed(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        resp = client.get("/api/v1/personality/presets", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()["presets"]) > 0

    def test_usage_quota(self, client: TestClient, signed_in_user) -> None:
        headers, _ = signed_in_user
        resp = client.get("/api/v1/usage/quota", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["tier"] == "free"

    def test_byok_put_unconfigured_503(self, client: TestClient, signed_in_user) -> None:
        # The default test Settings has no SECRET_ENCRYPTION_KEYS → storing a key fails loud.
        headers, _ = signed_in_user
        resp = client.post(
            "/api/v1/me/keys",
            json={"provider": "anthropic", "key": "sk-secret"},
            headers=headers,
        )
        assert resp.status_code == 503
        assert resp.json()["code"] == "secret_storage_unavailable"
