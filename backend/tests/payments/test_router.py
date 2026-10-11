"""HTTP-surface tests for the billing router: the plan catalog (with the publishable client
token), and the webhook endpoint's raw-body signature verification + entitlement application over
the real FastAPI boundary. Routes that make outbound Paddle calls (checkout/portal/history) are
covered at the service/client seam; here we prove the signature gate and the plans catalog."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from accounts.auth import issue_access_token
from api.routers.billing import get_billing_client, get_optional_billing_client, get_price_lookup
from config.settings import Settings
from payments.client import PaddleClient
from payments.pricing import clear_price_cache
from schemas.enums import Tier
from tests.api.conftest import build_test_client, sign_in
from tests.payments.conftest import (
    PRICE_PREMIUM_MONTHLY,
    PRICE_PRO_MONTHLY,
    WEBHOOK_SECRET,
)

pytestmark = pytest.mark.governance

_TEST_JWT_SECRET = "test-secret-not-a-real-key-0123456789abcdef"


@pytest.fixture
def billing_settings() -> Settings:
    return Settings(
        jwt_secret_key=_TEST_JWT_SECRET,
        env="development",
        paddle_api_key="pk-test",
        paddle_webhook_secret=WEBHOOK_SECRET,
        paddle_client_token="ptok-publishable",
        paddle_environment="sandbox",
        paddle_price_premium_monthly=PRICE_PREMIUM_MONTHLY,
        paddle_price_pro_monthly=PRICE_PRO_MONTHLY,
        # Everything else explicitly unset, so a developer's real backend/.env never leaks in.
        paddle_price_premium_annual=None,
        paddle_price_premium_byok_monthly=None,
        paddle_price_premium_byok_annual=None,
        paddle_price_pro_annual=None,
        paddle_price_pro_byok_monthly=None,
        paddle_price_pro_byok_annual=None,
    )


_STUB_PRICE_ROWS: list[dict[str, object]] = [
    {
        "id": PRICE_PREMIUM_MONTHLY,
        "unit_price": {"amount": "1099", "currency_code": "USD"},
        "unit_price_overrides": [
            {"country_codes": ["TW"], "unit_price": {"amount": "29900", "currency_code": "TWD"}}
        ],
    }
]


async def _stub_price_lookup(price_ids: list[str]) -> list[dict[str, object]]:
    return _STUB_PRICE_ROWS


@pytest.fixture
def client(billing_settings: Settings):
    with build_test_client(billing_settings, agent_answer="ok") as test_client:
        # Never reach Paddle from a test: the price source is a dependency.
        test_client.app.dependency_overrides[get_price_lookup] = lambda: _stub_price_lookup  # type: ignore[attr-defined]
        clear_price_cache()
        yield test_client


def test_plans_exposes_client_token_and_catalog(client: TestClient) -> None:
    resp = client.get("/api/v1/billing/plans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["client_token"] == "ptok-publishable"
    assert body["environment"] == "sandbox"
    prices = {p["price_id"] for p in body["plans"]}
    assert PRICE_PREMIUM_MONTHLY in prices


def test_plans_carry_paddles_price_and_the_country_override(client: TestClient) -> None:
    def premium_monthly(query: str = "") -> dict[str, object]:
        plans = client.get(f"/api/v1/billing/plans{query}").json()["plans"]
        return next(p for p in plans if p["price_id"] == PRICE_PREMIUM_MONTHLY)

    assert premium_monthly()["price"] == {"amount": "1099", "currency_code": "USD"}
    assert premium_monthly("?country=TW")["price"] == {"amount": "29900", "currency_code": "TWD"}


def test_a_malformed_country_is_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/billing/plans?country=TWN").status_code == 422


def _signed_headers(raw: bytes, secret: str = WEBHOOK_SECRET) -> dict[str, str]:
    ts = int(time.time())
    digest = hmac.new(secret.encode(), f"{ts}:".encode() + raw, hashlib.sha256).hexdigest()
    return {"Paddle-Signature": f"ts={ts};h1={digest}"}


def test_webhook_rejects_bad_signature(client: TestClient) -> None:
    raw = (
        b'{"event_id":"evt_1","event_type":"subscription.created",'
        b'"occurred_at":"2024-05-01T00:00:00Z","data":{}}'
    )
    resp = client.post(
        "/api/v1/billing/webhook/paddle",
        content=raw,
        headers={"Paddle-Signature": "ts=1;h1=deadbeef"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_signature"


def test_webhook_applies_entitlement_end_to_end(
    client: TestClient, billing_settings: Settings
) -> None:
    _, user_id = sign_in(client, billing_settings, tier=Tier.FREE)
    raw = (
        b'{"event_id":"evt_http_1","event_type":"subscription.created",'
        b'"occurred_at":"2024-05-01T00:00:00Z","notification_id":"ntf_1",'
        b'"data":{"id":"sub_http","customer_id":"ctm_1","status":"active",'
        b'"items":[{"price":{"id":"' + PRICE_PREMIUM_MONTHLY.encode() + b'"}}],'
        b'"custom_data":{"user_id":"' + user_id.encode() + b'"}}}'
    )
    resp = client.post("/api/v1/billing/webhook/paddle", content=raw, headers=_signed_headers(raw))
    assert resp.status_code == 200
    assert resp.json()["processed"] is True

    # The webhook is the only writer of tier; confirm it took effect via the authenticated /me.
    token = issue_access_token(user_id=user_id, settings=billing_settings)
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"]["tier"] == "premium"


def test_a_subscriber_cannot_start_a_second_checkout(
    client: TestClient, billing_settings: Settings
) -> None:
    _, user_id = sign_in(client, billing_settings, tier=Tier.FREE)
    raw = (
        b'{"event_id":"evt_http_2","event_type":"subscription.created",'
        b'"occurred_at":"2024-05-01T00:00:00Z","notification_id":"ntf_2",'
        b'"data":{"id":"sub_http2","customer_id":"ctm_2","status":"active",'
        b'"items":[{"price":{"id":"' + PRICE_PREMIUM_MONTHLY.encode() + b'"}}],'
        b'"custom_data":{"user_id":"' + user_id.encode() + b'"}}}'
    )
    assert client.post(
        "/api/v1/billing/webhook/paddle", content=raw, headers=_signed_headers(raw)
    ).json()["processed"]

    token = issue_access_token(user_id=user_id, settings=billing_settings)
    resp = client.post(
        "/api/v1/billing/checkout",
        json={"tier": "premium", "interval": "monthly", "byok": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Refused before any call to Paddle (the test has no Paddle stub, so reaching it would fail).
    assert resp.status_code == 409
    assert resp.json()["code"] == "already_subscribed"


# ---- subscription management (change plan / resume / invoice) ------------------------------------
_SUB = "sub_lifecycle"


class _FakePaddle:
    """A scripted Paddle API: records every call, serves the subscription the test configures."""

    def __init__(self) -> None:
        self.price_id = PRICE_PREMIUM_MONTHLY
        self.scheduled_change: dict[str, str] | None = None
        self.calls: list[tuple[str, str, object]] = []
        self.portal_sessions_fail = False
        self.cancel_fails = False
        self.status = "active"

    def entity(self) -> dict[str, object]:
        return {
            "id": _SUB,
            "customer_id": "ctm_1",
            "status": self.status,
            "items": [{"price": {"id": self.price_id}}],
            "current_billing_period": {
                "starts_at": "2026-10-06T00:00:00Z",
                "ends_at": "2026-11-06T00:00:00Z",
            },
            "scheduled_change": self.scheduled_change,
            "management_urls": {
                "update_payment_method": "https://portal.example/fallback-card",
                "cancel": "https://portal.example/fallback-cancel",
            },
            "custom_data": None,
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 - a route table
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path = request.url.path
        if request.method == "PATCH" and path == f"/subscriptions/{_SUB}":
            if body and body.get("items"):
                self.price_id = body["items"][0]["price_id"]
            if body and "scheduled_change" in body:
                self.scheduled_change = body["scheduled_change"]
            return httpx.Response(200, json={"data": self.entity()})
        if request.method == "POST" and path == f"/subscriptions/{_SUB}/cancel":
            if self.cancel_fails:
                return httpx.Response(500, json={"error": {}})
            self.status = "canceled"
            return httpx.Response(200, json={"data": self.entity()})
        if request.method == "POST" and path == f"/subscriptions/{_SUB}/resume":
            self.status = "active"
            return httpx.Response(200, json={"data": self.entity()})
        if request.method == "GET" and path == f"/subscriptions/{_SUB}":
            return httpx.Response(200, json={"data": self.entity()})
        if request.method == "POST" and path == "/customers/ctm_1/portal-sessions":
            if self.portal_sessions_fail:
                return httpx.Response(500, json={"error": {}})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "urls": {
                            "general": {"overview": "https://portal.example/overview"},
                            "subscriptions": [
                                {
                                    "id": _SUB,
                                    "update_subscription_payment_method": "https://portal.example/card",
                                }
                            ],
                        }
                    }
                },
            )
        if path == "/transactions":
            return httpx.Response(200, json={"data": [{"id": "txn_mine"}]})
        if path == "/transactions/txn_mine/invoice":
            return httpx.Response(200, json={"data": {"url": "https://paddle.example/invoice.pdf"}})
        return httpx.Response(404, json={"error": {}})


@pytest.fixture
def paddle(client: TestClient, billing_settings: Settings) -> _FakePaddle:
    fake = _FakePaddle()
    paddle_client = PaddleClient(
        billing_settings,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake)),
    )
    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_billing_client] = lambda: paddle_client
    overrides[get_optional_billing_client] = lambda: paddle_client
    return fake


def _auth(settings: Settings, user_id: str) -> dict[str, str]:
    token = issue_access_token(user_id=user_id, settings=settings)
    return {"Authorization": f"Bearer {token}"}


def _subscribe(
    client: TestClient, settings: Settings, *, scheduled_cancel: bool = False
) -> dict[str, str]:
    """A signed-in user with a live Premium subscription, created the way Paddle does: a webhook."""
    _, user_id = sign_in(client, settings, tier=Tier.FREE)
    change = (
        ',"scheduled_change":{"action":"cancel","effective_at":"2026-11-06T00:00:00Z"}'
        if scheduled_cancel
        else ""
    )
    raw = (
        b'{"event_id":"evt_lc_' + user_id.encode() + b'","event_type":"subscription.created",'
        b'"occurred_at":"2026-10-06T00:00:00Z","notification_id":"ntf_lc",'
        b'"data":{"id":"' + _SUB.encode() + b'","customer_id":"ctm_1","status":"active",'
        b'"items":[{"price":{"id":"'
        + PRICE_PREMIUM_MONTHLY.encode()
        + b'"}}]'
        + change.encode()
        + b',"custom_data":{"user_id":"'
        + user_id.encode()
        + b'"}}}'
    )
    assert client.post(
        "/api/v1/billing/webhook/paddle", content=raw, headers=_signed_headers(raw)
    ).json()["processed"]
    return _auth(settings, user_id)


def test_changing_plan_switches_the_price_and_refreshes_the_tier(
    client: TestClient, billing_settings: Settings, paddle: _FakePaddle
) -> None:
    headers = _subscribe(client, billing_settings)
    resp = client.post(
        "/api/v1/billing/change-plan",
        json={"tier": "pro", "interval": "monthly", "byok": False},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["subscription"]["tier"] == "pro"
    patch = next(c for c in paddle.calls if c[0] == "PATCH")
    assert patch[2] == {
        "items": [{"price_id": PRICE_PRO_MONTHLY, "quantity": 1}],
        "proration_billing_mode": "prorated_immediately",
    }
    assert client.get("/api/v1/me", headers=headers).json()["user"]["tier"] == "pro"


def test_change_plan_refuses_the_current_plan_an_unknown_plan_and_no_subscription(
    client: TestClient, billing_settings: Settings, paddle: _FakePaddle
) -> None:
    _, user_id = sign_in(client, billing_settings, tier=Tier.FREE)
    nobody = _auth(billing_settings, user_id)
    pro = {"tier": "pro", "interval": "monthly", "byok": False}
    no_sub = client.post("/api/v1/billing/change-plan", json=pro, headers=nobody)
    assert no_sub.status_code == 404

    headers = _subscribe(client, billing_settings)
    same = {"tier": "premium", "interval": "monthly", "byok": False}
    refused = client.post("/api/v1/billing/change-plan", json=same, headers=headers)
    assert refused.json()["code"] == "same_plan"
    unknown = {"tier": "pro", "interval": "annual", "byok": False}  # not configured in this env
    refused = client.post("/api/v1/billing/change-plan", json=unknown, headers=headers)
    assert refused.json()["code"] == "unknown_plan"
    assert not [c for c in paddle.calls if c[0] == "PATCH"]  # nothing reached Paddle


def test_a_scheduled_cancel_blocks_plan_changes_until_resumed(
    client: TestClient, billing_settings: Settings, paddle: _FakePaddle
) -> None:
    headers = _subscribe(client, billing_settings, scheduled_cancel=True)
    paddle.scheduled_change = {"action": "cancel", "effective_at": "2026-11-06T00:00:00Z"}
    pro = {"tier": "pro", "interval": "monthly", "byok": False}
    blocked = client.post("/api/v1/billing/change-plan", json=pro, headers=headers)
    assert blocked.json()["code"] == "cancel_scheduled"

    resumed = client.post("/api/v1/billing/resume", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["subscription"]["scheduled_cancel_at"] is None
    patch = next(c for c in paddle.calls if c[0] == "PATCH")
    assert patch[2] == {"scheduled_change": None}
    # Nothing left to resume now.
    assert (
        client.post("/api/v1/billing/resume", headers=headers).json()["code"] == "nothing_to_resume"
    )


def test_an_invoice_link_is_only_for_the_callers_own_transaction(
    client: TestClient, billing_settings: Settings, paddle: _FakePaddle
) -> None:
    headers = _subscribe(client, billing_settings)
    mine = client.get("/api/v1/billing/invoice/txn_mine", headers=headers)
    assert mine.status_code == 200
    assert mine.json() == {"url": "https://paddle.example/invoice.pdf"}
    theirs = client.get("/api/v1/billing/invoice/txn_someone_else", headers=headers)
    assert theirs.status_code == 404
    assert theirs.json()["code"] == "transaction_not_found"


def test_the_portal_opens_the_overview_or_the_card_form(
    client: TestClient, billing_settings: Settings, paddle: _FakePaddle
) -> None:
    headers = _subscribe(client, billing_settings)
    overview = client.post("/api/v1/billing/portal", headers=headers)
    assert overview.json() == {"portal_url": "https://portal.example/overview"}
    card = client.post("/api/v1/billing/portal?purpose=payment", headers=headers)
    assert card.json() == {"portal_url": "https://portal.example/card"}
    assert client.post("/api/v1/billing/portal?purpose=nope", headers=headers).status_code == 422


def test_the_portal_falls_back_to_the_subscriptions_own_links(
    client: TestClient, billing_settings: Settings, paddle: _FakePaddle
) -> None:
    headers = _subscribe(client, billing_settings)
    paddle.portal_sessions_fail = True
    resp = client.post("/api/v1/billing/portal", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"portal_url": "https://portal.example/fallback-card"}


def test_a_paused_subscription_can_be_resumed_but_not_changed(
    client: TestClient, billing_settings: Settings, paddle: _FakePaddle
) -> None:
    headers = _subscribe(client, billing_settings)
    paddle.status = "paused"
    # Reading the subscription reconciles with Paddle, so the local row now says paused.
    assert (
        client.get("/api/v1/billing/subscription", headers=headers).json()["subscription"]["status"]
        == "paused"
    )
    assert client.get("/api/v1/me", headers=headers).json()["user"]["tier"] == "free"

    pro = {"tier": "pro", "interval": "monthly", "byok": False}
    blocked = client.post("/api/v1/billing/change-plan", json=pro, headers=headers)
    assert blocked.json()["code"] == "paused"

    resumed = client.post("/api/v1/billing/resume", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["subscription"]["status"] == "active"
    assert ("POST", f"/subscriptions/{_SUB}/resume", {"effective_from": "immediately"}) in (
        paddle.calls
    )
    assert client.get("/api/v1/me", headers=headers).json()["user"]["tier"] == "premium"


def test_plans_response_carries_the_tier_comparison(client) -> None:
    tiers = client.get("/api/v1/billing/plans").json()["tiers"]
    assert [t["tier"] for t in tiers] == ["free", "premium", "pro"]
    assert tiers[0]["chat_limit_per_day"] == 3
    assert tiers[2]["chat_limit_per_day"] is None
    assert tiers[2]["deep_research_limit_per_day"] == 5
    assert "deep_research" not in tiers[1]["features"]


# ---- account deletion -----------------------------------------------------------------------


def test_deleting_the_account_cancels_billing_then_erases_the_user(
    client: TestClient, billing_settings: Settings, paddle: _FakePaddle
) -> None:
    headers = _subscribe(client, billing_settings)

    response = client.request("DELETE", "/api/v1/me", json={"confirm": "DELETE"}, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert ("POST", f"/subscriptions/{_SUB}/cancel", {"effective_from": "immediately"}) in (
        paddle.calls
    )
    # The user, and the access token minted for them, no longer resolve to anyone.
    assert client.get("/api/v1/me", headers=headers).status_code == 401


def test_a_failed_billing_cancel_aborts_the_deletion_and_erases_nothing(
    client: TestClient, billing_settings: Settings, paddle: _FakePaddle
) -> None:
    headers = _subscribe(client, billing_settings)
    paddle.cancel_fails = True

    response = client.request("DELETE", "/api/v1/me", json={"confirm": "DELETE"}, headers=headers)

    assert response.status_code == 502
    assert response.json()["code"] == "billing_cancel_failed"
    assert response.json()["retryable"] is True
    assert client.get("/api/v1/me", headers=headers).status_code == 200


def test_the_confirmation_word_is_required(
    client: TestClient, billing_settings: Settings, paddle: _FakePaddle
) -> None:
    headers = _subscribe(client, billing_settings)

    response = client.request("DELETE", "/api/v1/me", json={"confirm": "yes"}, headers=headers)

    assert response.status_code == 422
    assert paddle.calls == [] or all("/cancel" not in path for _, path, _ in paddle.calls)
    assert client.get("/api/v1/me", headers=headers).status_code == 200


def test_a_user_without_a_subscription_is_erased_without_calling_paddle(
    client: TestClient, billing_settings: Settings, paddle: _FakePaddle
) -> None:
    headers, _ = sign_in(client, billing_settings, tier=Tier.FREE)

    response = client.request("DELETE", "/api/v1/me", json={"confirm": "DELETE"}, headers=headers)

    assert response.status_code == 200
    assert paddle.calls == []


def test_deleting_requires_sign_in(client: TestClient) -> None:
    response = client.request("DELETE", "/api/v1/me", json={"confirm": "DELETE"})

    assert response.status_code == 401
