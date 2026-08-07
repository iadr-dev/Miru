"""HTTP tests for the scheduler surface: Pro-only feature gating, schedule/alert CRUD with
per-user scoping, notifications, alert preferences, and idempotent device registration."""

from __future__ import annotations

from fastapi.testclient import TestClient

from config.settings import Settings
from schemas.enums import Tier
from tests.api.conftest import sign_in


def _schedule_body() -> dict[str, object]:
    return {
        "trigger": {"type": "market_event", "spec": "after_close", "market": "TW"},
        "action": {"skill_ref": "daily-context-brief", "inputs": {"stock_ids": ["2330"]}},
        "delivery_channels": ["in_app"],
    }


class TestFeatureGating:
    def test_schedules_require_signin(self, client: TestClient) -> None:
        assert client.get("/api/v1/schedules").status_code == 401

    def test_free_user_forbidden(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        resp = client.post("/api/v1/schedules", json=_schedule_body(), headers=headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == "feature_not_entitled"

    def test_pro_user_can_create(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        resp = client.post("/api/v1/schedules", json=_schedule_body(), headers=headers)
        assert resp.status_code == 201
        assert resp.json()["action"]["skill_ref"] == "daily-context-brief"


class TestScheduleValidation:
    def test_wildcard_cron_rejected(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        body = _schedule_body()
        body["trigger"] = {"type": "cron", "spec": "* * * * *", "market": "TW"}
        resp = client.post("/api/v1/schedules", json=body, headers=headers)
        assert resp.status_code == 422
        assert resp.json()["code"] == "invalid_trigger"

    def test_unknown_skill_rejected(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        body = _schedule_body()
        body["action"] = {"skill_ref": "no-such-skill", "inputs": {}}
        resp = client.post("/api/v1/schedules", json=body, headers=headers)
        assert resp.status_code == 422
        assert resp.json()["code"] == "unknown_skill"


class TestScheduleCrudIsolation:
    def test_crud_and_isolation(self, client: TestClient, settings: Settings) -> None:
        headers_a, _ = sign_in(client, settings, tier=Tier.PRO)
        headers_b, _ = sign_in(client, settings, tier=Tier.PRO)

        created = client.post("/api/v1/schedules", json=_schedule_body(), headers=headers_a)
        job_id = created.json()["id"]

        # user_b cannot see or patch it
        assert client.get("/api/v1/schedules", headers=headers_b).json()["schedules"] == []
        patched_b = client.patch(
            f"/api/v1/schedules/{job_id}", json={"enabled": False}, headers=headers_b
        )
        assert patched_b.status_code == 404

        patched_a = client.patch(
            f"/api/v1/schedules/{job_id}", json={"enabled": False}, headers=headers_a
        )
        assert patched_a.status_code == 200
        assert patched_a.json()["enabled"] is False

        assert client.delete(f"/api/v1/schedules/{job_id}", headers=headers_a).status_code == 204


class TestAlerts:
    def test_create_list_delete(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        body = {
            "symbol": "2330",
            "market": "TW",
            "condition": {
                "type": "institutional",
                "metric": "foreign_net",
                "comparator": "streak_up",
                "threshold": 3,
                "window": 3,
            },
            "channels": ["in_app"],
        }
        created = client.post("/api/v1/alerts", json=body, headers=headers)
        assert created.status_code == 201
        alert_id = created.json()["id"]

        listed = client.get("/api/v1/alerts", headers=headers)
        assert any(a["id"] == alert_id for a in listed.json()["alerts"])

        assert client.delete(f"/api/v1/alerts/{alert_id}", headers=headers).status_code == 204


class TestNotificationsAndPreferences:
    def test_badge_and_list_empty(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        badge = client.get("/api/v1/notifications/badge", headers=headers)
        assert badge.status_code == 200
        assert badge.json()["unread_notifications"] == 0
        listed = client.get("/api/v1/notifications", headers=headers)
        assert listed.json()["notifications"] == []

    def test_put_alert_preferences(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        resp = client.put(
            "/api/v1/me/alert-preferences",
            json={
                "channels": ["in_app", "line"],
                "enabled_categories": ["price"],
                "morning_digest": True,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["morning_digest"] is True


class TestDevices:
    def test_register_is_idempotent(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)  # device reg is not Pro-gated
        first = client.post(
            "/api/v1/me/devices", json={"platform": "ios", "token": "tok-1"}, headers=headers
        )
        assert first.status_code == 201
        second = client.post(
            "/api/v1/me/devices", json={"platform": "ios", "token": "tok-1"}, headers=headers
        )
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

        assert client.delete("/api/v1/me/devices/tok-1", headers=headers).status_code == 204
