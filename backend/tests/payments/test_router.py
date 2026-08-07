"""HTTP-surface tests for the billing router: the plan catalog (with the publishable client
token), and the webhook endpoint's raw-body signature verification + entitlement application over
the real FastAPI boundary. Routes that make outbound Paddle calls (checkout/portal/history) are
covered at the service/client seam; here we prove the signature gate and the plans catalog."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from accounts.auth import issue_access_token
from config.settings import Settings
from schemas.enums import Tier
from tests.api.conftest import build_test_client, sign_in
from tests.payments.conftest import (
    PRICE_PREMIUM_MONTHLY,
    WEBHOOK_SECRET,
)

pytestmark = pytest.mark.governance

_TEST_JWT_SECRET = "test-secret-not-a-real-key-0123456789abcdef"


@pytest.fixture
def billing_settings() -> Settings:
    return Settings(
        jwt_secret_key=_TEST_JWT_SECRET,
        aitw_env="development",
        paddle_api_key="pk-test",
        paddle_webhook_secret=WEBHOOK_SECRET,
        paddle_client_token="ptok-publishable",
        paddle_environment="sandbox",
        paddle_price_premium_monthly=PRICE_PREMIUM_MONTHLY,
    )


@pytest.fixture
def client(billing_settings: Settings):
    with build_test_client(billing_settings, agent_answer="ok") as test_client:
        yield test_client


def test_plans_exposes_client_token_and_catalog(client: TestClient) -> None:
    resp = client.get("/api/v1/billing/plans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["client_token"] == "ptok-publishable"
    prices = {p["price_id"] for p in body["plans"]}
    assert PRICE_PREMIUM_MONTHLY in prices


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
