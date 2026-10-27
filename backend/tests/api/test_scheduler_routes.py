"""HTTP tests for the scheduler surface: open to every signed-in tier (modules/14), schedule/alert
CRUD with per-user scoping, fair-use caps, notifications, alert preferences, and idempotent
device registration."""

from __future__ import annotations

import json
from urllib.parse import quote

from fastapi.testclient import TestClient

from config.settings import Settings
from scheduler.caps import MAX_ALERT_RULES, MAX_SCHEDULES
from schemas.enums import Tier
from tests.api.conftest import sign_in


def _alert_body() -> dict[str, object]:
    return {
        "symbol": "2330",
        "market": "TW",
        "condition": {"kind": "close_price", "comparator": "above", "value": 600.5},
        "channels": ["in_app"],
    }


def _schedule_body() -> dict[str, object]:
    return {
        "trigger": {"type": "market_event", "spec": "after_close", "market": "TW"},
        "action": {"skill_ref": "daily-context-brief", "inputs": {"stock_ids": ["2330"]}},
        "delivery_channels": ["in_app"],
    }


class TestOpenToAllTiers:
    def test_schedules_require_signin(self, client: TestClient) -> None:
        assert client.get("/api/v1/schedules").status_code == 401

    def test_free_user_can_create_and_gets_a_next_run(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        resp = client.post("/api/v1/schedules", json=_schedule_body(), headers=headers)
        assert resp.status_code == 201
        # Without a next_run the worker's due_jobs scan would never see the job (v1 bug).
        assert resp.json()["next_run"] is not None

    def test_free_user_can_create_alert(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        resp = client.post("/api/v1/alerts", json=_alert_body(), headers=headers)
        assert resp.status_code == 201

    def test_pro_user_can_create(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        resp = client.post("/api/v1/schedules", json=_schedule_body(), headers=headers)
        assert resp.status_code == 201
        assert resp.json()["action"]["skill_ref"] == "daily-context-brief"


def _weekly_review_body(spec: str = "0 9 * * 6") -> dict[str, object]:
    return {
        "trigger": {"type": "cron", "spec": spec, "market": "TW"},
        "action": {"skill_ref": "watchlist-review", "inputs": {"review": "weekly"}},
        "delivery_channels": ["in_app"],
    }


class TestWeeklyWatchlistReview:
    def test_every_tier_can_schedule_it_and_it_fires_on_the_chosen_weekday(
        self, client: TestClient, settings: Settings
    ) -> None:
        for tier in (Tier.FREE, Tier.PRO):
            headers, _ = sign_in(client, settings, tier=tier)
            resp = client.post(
                "/api/v1/schedules", json=_weekly_review_body("30 20 * * 0"), headers=headers
            )
            assert resp.status_code == 201
            assert resp.json()["action"] == {
                "skill_ref": "watchlist-review",
                "inputs": {"review": "weekly"},
            }
            assert resp.json()["next_run"] is not None

    def test_a_wildcard_time_is_refused(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        resp = client.post(
            "/api/v1/schedules", json=_weekly_review_body("* 9 * * 6"), headers=headers
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "invalid_trigger"

    def test_it_counts_toward_the_schedule_cap(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        for _ in range(MAX_SCHEDULES):
            assert (
                client.post(
                    "/api/v1/schedules", json=_weekly_review_body(), headers=headers
                ).status_code
                == 201
            )
        over = client.post("/api/v1/schedules", json=_weekly_review_body(), headers=headers)
        assert over.status_code == 409
        assert over.json()["code"] == "cap_reached"


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


class TestScheduleEdit:
    def test_edit_trigger_recomputes_next_run_and_bumps_version(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        created = client.post("/api/v1/schedules", json=_schedule_body(), headers=headers).json()

        resp = client.patch(
            f"/api/v1/schedules/{created['id']}",
            json={
                "trigger": {"type": "market_event", "spec": "pre_open", "market": "TW"},
                "expected_version": created["version"],
            },
            headers=headers,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["trigger"]["spec"] == "pre_open"
        assert body["version"] == created["version"] + 1
        assert body["next_run"] != created["next_run"]

    def test_stale_edit_is_a_409(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        created = client.post("/api/v1/schedules", json=_schedule_body(), headers=headers).json()
        url = f"/api/v1/schedules/{created['id']}"
        assert client.patch(url, json={"enabled": False}, headers=headers).status_code == 200

        stale = client.patch(
            url, json={"enabled": True, "expected_version": created["version"]}, headers=headers
        )

        assert stale.status_code == 409
        assert stale.json()["code"] == "stale_version"

    def test_edit_rejects_an_unknown_skill_and_unknown_fields(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        created = client.post("/api/v1/schedules", json=_schedule_body(), headers=headers).json()
        url = f"/api/v1/schedules/{created['id']}"

        bad_skill = client.patch(
            url, json={"action": {"skill_ref": "no-such-skill", "inputs": {}}}, headers=headers
        )
        assert bad_skill.status_code == 422
        assert bad_skill.json()["code"] == "unknown_skill"

        assert client.patch(url, json={"bogus": 1}, headers=headers).status_code == 422

    def test_reenabling_a_paused_schedule_resumes_it(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        created = client.post("/api/v1/schedules", json=_schedule_body(), headers=headers).json()
        url = f"/api/v1/schedules/{created['id']}"
        client.patch(url, json={"enabled": False}, headers=headers)

        resumed = client.patch(url, json={"enabled": True}, headers=headers).json()

        assert resumed["enabled"] is True
        assert resumed["next_run"] is not None


class TestUnsupportedMarket:
    """Schedules are TW/US only (the table's CHECK). Another market is a 422 at the API — it used to
    pass validation and fail the CHECK at insert as an unhandled 500."""

    def test_schedule_in_an_unsupported_market_is_422(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        body = _schedule_body()
        body["trigger"] = {"type": "market_event", "spec": "after_close", "market": "JP"}
        resp = client.post("/api/v1/schedules", json=body, headers=headers)
        assert resp.status_code == 422


class TestAlerts:
    def test_create_list_delete(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.PRO)
        body = {
            "symbol": "2330",
            "market": "TW",
            "condition": {
                "kind": "institutional_streak",
                "investor": "foreign",
                "direction": "buy",
                "days": 3,
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


class TestFairUseCaps:
    def test_schedule_cap_returns_409(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        for _ in range(MAX_SCHEDULES):
            assert (
                client.post("/api/v1/schedules", json=_schedule_body(), headers=headers).status_code
                == 201
            )
        resp = client.post("/api/v1/schedules", json=_schedule_body(), headers=headers)
        assert resp.status_code == 409
        assert resp.json()["code"] == "cap_reached"

    def test_cap_is_per_user(self, client: TestClient, settings: Settings) -> None:
        headers_a, _ = sign_in(client, settings, tier=Tier.FREE)
        headers_b, _ = sign_in(client, settings, tier=Tier.FREE)
        for _ in range(MAX_SCHEDULES):
            client.post("/api/v1/schedules", json=_schedule_body(), headers=headers_a)
        resp = client.post("/api/v1/schedules", json=_schedule_body(), headers=headers_b)
        assert resp.status_code == 201


class TestCronValidation:
    def test_unparseable_cron_rejected(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        body = _schedule_body()
        body["trigger"] = {"type": "cron", "spec": "30 99 * * *", "market": "TW"}
        resp = client.post("/api/v1/schedules", json=body, headers=headers)
        assert resp.status_code == 422
        assert resp.json()["code"] == "invalid_trigger"


class TestMarkReadAndPreferences:
    def test_mark_read_requires_exactly_one_selector(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        both = client.post(
            "/api/v1/notifications/read", json={"ids": ["x"], "all": True}, headers=headers
        )
        neither = client.post("/api/v1/notifications/read", json={}, headers=headers)
        assert both.status_code == 422
        assert neither.status_code == 422

    def test_mark_all_read_is_idempotent(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        first = client.post("/api/v1/notifications/read", json={"all": True}, headers=headers)
        second = client.post("/api/v1/notifications/read", json={"all": True}, headers=headers)
        assert first.status_code == 200
        assert second.json() == {"marked": 0}

    def test_get_preferences_defaults_then_reflects_put(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        default = client.get("/api/v1/me/alert-preferences", headers=headers)
        assert default.status_code == 200
        assert default.json()["channels"] == []
        # Never saved: the default preset is quiet midnight-08:00, GMT+8.
        assert default.json()["quiet_hours"] == {"start": "00:00", "end": "08:00"}
        assert default.json()["timezone"] == "Asia/Taipei"
        client.put(
            "/api/v1/me/alert-preferences",
            json={"channels": ["in_app"], "quiet_hours": {"start": "00:00", "end": "00:00"}},
            headers=headers,
        )
        saved = client.get("/api/v1/me/alert-preferences", headers=headers).json()
        assert saved["channels"] == ["in_app"]
        # An empty window is how quiet hours are switched off; it is kept, not defaulted.
        assert saved["quiet_hours"] == {"start": "00:00", "end": "00:00"}


class TestQuietHoursDefault:
    def test_a_saved_row_with_no_window_still_shows_the_default_preset(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        client.put("/api/v1/me/alert-preferences", json={"channels": ["in_app"]}, headers=headers)
        saved = client.get("/api/v1/me/alert-preferences", headers=headers).json()
        assert saved["quiet_hours"] == {"start": "00:00", "end": "08:00"}
        assert saved["timezone"] == "Asia/Taipei"


class TestAlertScopeAndTemplates:
    def test_decimal_threshold_round_trips(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        created = client.post("/api/v1/alerts", json=_alert_body(), headers=headers).json()
        assert created["condition"]["value"] == 600.5  # v1 truncated this to 600
        assert created["expires_at"] is not None  # default lifetime applied
        assert created["trigger_mode"] == "once_per_day"

    def test_never_expires_when_null(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        body = {**_alert_body(), "expires_in_days": None}
        assert (
            client.post("/api/v1/alerts", json=body, headers=headers).json()["expires_at"] is None
        )

    def test_symbol_and_watchlist_together_rejected(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        body = {**_alert_body(), "watchlist_id": "wl-1"}
        assert client.post("/api/v1/alerts", json=body, headers=headers).status_code == 422

    def test_neither_scope_rejected(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        body = {k: v for k, v in _alert_body().items() if k not in ("symbol", "market")}
        assert client.post("/api/v1/alerts", json=body, headers=headers).status_code == 422

    def test_unknown_watchlist_is_404(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        body = {k: v for k, v in _alert_body().items() if k not in ("symbol", "market")}
        body["watchlist_id"] = "does-not-exist"
        # A group rule uses a scale-free condition (a price level is refused on a group).
        body["condition"] = {"kind": "daily_change", "direction": "up", "percent": 5}
        resp = client.post("/api/v1/alerts", json=body, headers=headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == "watchlist_not_found"

    def test_unsupported_market_rejected(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        body = {**_alert_body(), "market": "JP"}
        resp = client.post("/api/v1/alerts", json=body, headers=headers)
        assert resp.status_code == 422
        assert "market" in resp.text

    def test_legacy_free_text_condition_rejected(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        legacy = {"type": "price", "metric": "close", "comparator": "above", "threshold": 600}
        body = {**_alert_body(), "condition": legacy}
        assert client.post("/api/v1/alerts", json=body, headers=headers).status_code == 422

    def test_out_of_range_template_params_rejected(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        condition = {
            "kind": "institutional_streak",
            "investor": "foreign",
            "direction": "buy",
            "days": 1,
        }
        body = {**_alert_body(), "condition": condition}
        assert client.post("/api/v1/alerts", json=body, headers=headers).status_code == 422


class TestAlertEdit:
    def _create(self, client: TestClient, headers: dict[str, str]) -> dict[str, object]:
        return client.post("/api/v1/alerts", json=_alert_body(), headers=headers).json()

    def _patch(
        self, client: TestClient, headers: dict[str, str], alert_id: object, body: dict[str, object]
    ):
        return client.patch(f"/api/v1/alerts/{alert_id}", json=body, headers=headers)

    def test_edit_condition_bumps_version_and_resets_state(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        created = self._create(client, headers)
        assert created["version"] == 0
        edited = self._patch(
            client,
            headers,
            created["id"],
            {
                "expected_version": 0,
                "condition": {"kind": "daily_change", "direction": "down", "percent": 7.5},
            },
        )
        assert edited.status_code == 200
        body = edited.json()
        assert body["version"] == 1
        assert body["condition"] == {"kind": "daily_change", "direction": "down", "percent": 7.5}
        assert body["state"] == {}

    def test_stale_expected_version_is_409_and_changes_nothing(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        created = self._create(client, headers)
        first = self._patch(
            client, headers, created["id"], {"expected_version": 0, "explain": True}
        )
        assert first.status_code == 200
        stale = self._patch(
            client, headers, created["id"], {"expected_version": 0, "explain": False}
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "stale_version"
        current = client.get("/api/v1/alerts", headers=headers).json()["alerts"][0]
        assert current["explain"] is True  # the losing edit did not land
        assert current["version"] == 1

    def test_omitted_leaves_alone_and_null_clears(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        created = self._create(client, headers)
        assert created["expires_at"] is not None
        untouched = self._patch(client, headers, created["id"], {"explain": True}).json()
        assert untouched["expires_at"] is not None  # omitted -> unchanged
        cleared = self._patch(client, headers, created["id"], {"expires_in_days": None}).json()
        assert cleared["expires_at"] is None  # explicit null -> never expires
        redated = self._patch(client, headers, created["id"], {"expires_in_days": 30}).json()
        assert redated["expires_at"] is not None

    def test_toggle_only_still_works_without_expected_version(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        created = self._create(client, headers)
        off = self._patch(client, headers, created["id"], {"enabled": False}).json()
        assert off["enabled"] is False
        assert off["version"] == 1

    def test_scope_cannot_be_edited(self, client: TestClient, settings: Settings) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        created = self._create(client, headers)
        for forbidden in ({"symbol": "2317"}, {"market": "US"}, {"watchlist_id": "x"}):
            resp = self._patch(client, headers, created["id"], forbidden)
            assert resp.status_code == 422, forbidden

    def test_invalid_condition_params_rejected(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        created = self._create(client, headers)
        bad = {"condition": {"kind": "close_price", "comparator": "above", "value": -1}}
        assert self._patch(client, headers, created["id"], bad).status_code == 422

    def test_other_users_alert_is_404(self, client: TestClient, settings: Settings) -> None:
        headers_a, _ = sign_in(client, settings, tier=Tier.FREE)
        headers_b, _ = sign_in(client, settings, tier=Tier.FREE)
        created = self._create(client, headers_a)
        resp = self._patch(client, headers_b, created["id"], {"enabled": False})
        assert resp.status_code == 404


class TestSymbolNormalisation:
    def test_tickers_are_stored_trimmed_and_upper_case(
        self, client: TestClient, settings: Settings
    ) -> None:
        # The ingested partitions key on the exchange ticker; 'aapl' would never match 'AAPL'.
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        body = {**_alert_body(), "symbol": "  aapl ", "market": "US"}
        created = client.post("/api/v1/alerts", json=body, headers=headers)
        assert created.status_code == 201
        assert created.json()["symbol"] == "AAPL"

    def test_taiwan_etf_suffix_letters_are_upper_cased_too(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        body = {**_alert_body(), "symbol": "00631l"}
        assert (
            client.post("/api/v1/alerts", json=body, headers=headers).json()["symbol"] == "00631L"
        )


class TestTwOnlyTemplates:
    _TW_ONLY = (
        {"kind": "attention_entry"},
        {"kind": "disposition_entry"},
        {"kind": "month_revenue_published"},
        {"kind": "margin_balance_change", "side": "margin", "direction": "up", "percent": 10},
        {"kind": "etf_premium_discount", "direction": "premium", "percent": 1},
        {"kind": "institutional_streak", "investor": "foreign", "direction": "buy", "days": 3},
    )

    def test_each_is_refused_for_a_us_symbol_instead_of_creating_a_dead_rule(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        for condition in self._TW_ONLY:
            body = {**_alert_body(), "symbol": "AAPL", "market": "US", "condition": condition}
            resp = client.post("/api/v1/alerts", json=body, headers=headers)
            assert resp.status_code == 422, condition
            assert resp.json()["code"] == "template_market_mismatch"
        assert client.get("/api/v1/alerts", headers=headers).json()["alerts"] == []

    def test_each_is_accepted_for_a_taiwan_symbol(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        for condition in self._TW_ONLY:
            body = {**_alert_body(), "condition": condition}
            assert client.post("/api/v1/alerts", json=body, headers=headers).status_code == 201

    def test_price_templates_still_work_for_us(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        body = {**_alert_body(), "symbol": "AAPL", "market": "US"}
        assert client.post("/api/v1/alerts", json=body, headers=headers).status_code == 201


class TestSymbolCheck:
    def test_requires_signin(self, client: TestClient) -> None:
        resp = client.get("/api/v1/alerts/symbol-check", params={"symbol": "AAPL", "market": "US"})
        assert resp.status_code == 401

    def test_reports_whether_the_ticker_has_price_data(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        resp = client.get(
            "/api/v1/alerts/symbol-check",
            params={"symbol": "notareal", "market": "US"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"checked", "exists"}
        assert body["exists"] is False  # the test router carries no bar for this ticker


class TestPreferenceTimezone:
    def test_timezone_round_trips_and_an_unknown_zone_is_rejected(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        saved = client.put(
            "/api/v1/me/alert-preferences",
            json={"channels": ["in_app"], "timezone": "America/New_York"},
            headers=headers,
        )
        assert saved.status_code == 200
        assert client.get("/api/v1/me/alert-preferences", headers=headers).json()["timezone"] == (
            "America/New_York"
        )
        bad = client.put(
            "/api/v1/me/alert-preferences", json={"timezone": "Mars/Base"}, headers=headers
        )
        assert bad.status_code == 422


class TestBrowserDevices:
    def test_a_long_json_subscription_registers_and_deletes(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        subscription = (
            '{"endpoint":"https://fcm.googleapis.com/fcm/send/abc/def?x=1",'
            '"keys":{"p256dh":"' + "p" * 90 + '","auth":"' + "a" * 24 + '"}}'
        )
        assert len(subscription) > 150
        created = client.post(
            "/api/v1/me/devices",
            json={"platform": "web", "token": subscription},
            headers=headers,
        )
        assert created.status_code == 201

        removed = client.delete(
            f"/api/v1/me/devices/{quote(subscription, safe='')}", headers=headers
        )
        assert removed.status_code == 204

    def test_a_subscription_pointing_anywhere_but_a_push_service_is_refused(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        for endpoint in (
            "http://169.254.169.254/latest/meta-data",
            "https://localhost/push",
            "https://10.0.0.5/push",
            "https://fcm.googleapis.com.evil.example/x",
            "https://user:pw@fcm.googleapis.com/x",
            "https://fcm.googleapis.com:8443/x",
        ):
            body = {"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}}
            resp = client.post(
                "/api/v1/me/devices",
                json={"platform": "web", "token": json.dumps(body)},
                headers=headers,
            )
            assert resp.status_code == 422, endpoint
            assert resp.json()["code"] == "invalid_push_subscription"


def _price(symbol: str, value: float, market: str = "TW") -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": market,
        "condition": {"kind": "close_price", "comparator": "above", "value": value},
    }


def _group(client: TestClient, headers: dict[str, str]) -> str:
    group = client.post(
        "/api/v1/watchlists",
        json={"name": "半導體", "market": "TW", "asset_class": "stock"},
        headers=headers,
    )
    assert group.status_code in (200, 201), group.text
    return str(group.json()["id"])


class TestPerStockAlerts:
    def test_a_group_rule_refuses_a_price_level_but_accepts_a_relative_condition(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        group_id = _group(client, headers)
        price = {
            "watchlist_id": group_id,
            "condition": {"kind": "close_price", "comparator": "above", "value": 600},
        }
        refused = client.post("/api/v1/alerts", json=price, headers=headers)
        assert refused.status_code == 422
        assert refused.json()["code"] == "template_scope_mismatch"

        relative = {
            "watchlist_id": group_id,
            "condition": {"kind": "daily_change", "direction": "up", "percent": 5},
        }
        assert client.post("/api/v1/alerts", json=relative, headers=headers).status_code == 201

    def test_a_batch_creates_one_rule_per_stock_each_with_its_own_level(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        group_id = _group(client, headers)
        body = {
            "origin_watchlist_id": group_id,
            "items": [_price("2330", 2600), _price("2454", 1350.5)],
        }
        resp = client.post("/api/v1/alerts/batch", json=body, headers=headers)
        assert resp.status_code == 201
        alerts = {a["symbol"]: a for a in resp.json()["alerts"]}
        assert alerts["2330"]["condition"]["value"] == 2600
        assert alerts["2454"]["condition"]["value"] == 1350.5
        assert all(a["origin_watchlist_id"] == group_id for a in alerts.values())
        assert all(
            a["watchlist_id"] is None for a in alerts.values()
        )  # per stock, not group-scoped
        listed = client.get("/api/v1/alerts", headers=headers).json()["alerts"]
        assert len(listed) == 2

    def test_a_batch_is_all_or_nothing_against_the_cap(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        first = [_price(f"{1000 + i}", 10 + i) for i in range(MAX_ALERT_RULES - 1)]
        for chunk_start in range(0, len(first), 30):
            chunk = first[chunk_start : chunk_start + 30]
            assert (
                client.post(
                    "/api/v1/alerts/batch", json={"items": chunk}, headers=headers
                ).status_code
                == 201
            )
        over = client.post(
            "/api/v1/alerts/batch",
            json={"items": [_price("3000", 1), _price("3001", 2)]},
            headers=headers,
        )
        assert over.status_code == 409
        assert over.json()["code"] == "cap_reached"
        total = len(client.get("/api/v1/alerts", headers=headers).json()["alerts"])
        assert total == MAX_ALERT_RULES - 1  # neither of the two was created

    def test_a_batch_rejects_a_duplicate_stock_a_foreign_group_and_a_us_only_gap(
        self, client: TestClient, settings: Settings
    ) -> None:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)
        dup = {"items": [_price("2330", 1), _price("2330", 2)]}
        assert client.post("/api/v1/alerts/batch", json=dup, headers=headers).status_code == 422
        foreign = {"items": [_price("2330", 1)], "origin_watchlist_id": "nope"}
        resp = client.post("/api/v1/alerts/batch", json=foreign, headers=headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == "watchlist_not_found"
        us_tw_only = {
            "items": [
                {
                    "symbol": "AAPL",
                    "market": "US",
                    "condition": {"kind": "attention_entry"},
                }
            ]
        }
        resp = client.post("/api/v1/alerts/batch", json=us_tw_only, headers=headers)
        assert resp.json()["code"] == "template_market_mismatch"
