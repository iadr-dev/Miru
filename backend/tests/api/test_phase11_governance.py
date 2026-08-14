"""Governance guarantees for the Phase-11 routers over the HTTP boundary: the market-REST
surface dispatches through `tools.base.dispatch_tool` (so the sponsor gate is never bypassable),
and the BYOK key store never logs the raw key. Marked `governance` for the highest-priority suite.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from accounts.crypto import generate_key
from config.settings import Settings
from schemas.enums import Tier
from tests.api.conftest import build_test_client, sign_in


@pytest.fixture
def byok_settings() -> Settings:
    return Settings(
        jwt_secret_key="test-secret-not-a-real-key-0123456789abcdef",
        env="development",
        google_client_id=None,
        google_client_secret=None,
        google_redirect_uri=None,
        secret_encryption_keys=generate_key(),
    )


@pytest.fixture
def byok_client(byok_settings: Settings, agent_answer: str):
    with build_test_client(byok_settings, agent_answer=agent_answer) as test_client:
        yield test_client


@pytest.mark.governance
def test_macro_sponsor_masked_for_free(client: TestClient, signed_in_user) -> None:
    # get_macro_indicators is a Sponsor-tier tool — a Free caller gets the masked teaser at 200.
    headers, _ = signed_in_user
    resp = client.get("/api/v1/macro", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["locked"] is True


@pytest.mark.governance
def test_macro_unmasked_for_premium(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.PREMIUM)
    resp = client.get("/api/v1/macro", headers=headers)
    assert resp.status_code == 200
    assert "locked" not in resp.json()


@pytest.mark.governance
def test_institutional_ranking_public_anonymous(client: TestClient) -> None:
    # A public market tool is served to an anonymous caller (no masking).
    resp = client.get("/api/v1/market/institutional-ranking")
    assert resp.status_code == 200
    assert "locked" not in resp.json()


@pytest.mark.governance
def test_search_tw_not_supported(client: TestClient) -> None:
    # No TW symbol-universe search backing exists — the route says so rather than faking it.
    resp = client.get("/api/v1/search", params={"q": "台積", "market": "TW"})
    assert resp.status_code == 501
    assert resp.json()["code"] == "not_supported"


@pytest.mark.governance
def test_byok_put_delete_never_logs_raw_key(
    byok_client: TestClient, byok_settings: Settings, caplog
) -> None:
    headers, _ = sign_in(byok_client, byok_settings, tier=Tier.FREE)
    raw_key = "sk-super-secret-value-xyz"
    with caplog.at_level(logging.DEBUG):
        put = byok_client.post(
            "/api/v1/me/keys",
            json={"provider": "anthropic", "key": raw_key},
            headers=headers,
        )
        assert put.status_code == 204
        # The BYOK key now lifts the chat limit (unlimited) — visible via usage/quota.
        quota = byok_client.get("/api/v1/usage/quota", headers=headers).json()
        assert quota["byok_active"] is True
        assert quota["chat_limit_per_day"] is None

        deleted = byok_client.delete("/api/v1/me/keys/anthropic", headers=headers)
        assert deleted.status_code == 204

    assert raw_key not in caplog.text
