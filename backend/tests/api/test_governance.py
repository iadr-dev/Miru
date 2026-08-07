"""Governance guarantees over the HTTP boundary.

The sponsor-entitlement gate is owned by `tools.base.dispatch_tool`; these tests prove the FastAPI
surface routes through it unchanged — a Free caller gets the masked teaser (HTTP 200, never a
401/403), a Premium caller gets the real data — so the interface cannot become a bypass around the
gate. Marked `governance` so it runs in the highest-priority suite.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from schemas.enums import Tier
from tests.api.conftest import build_sponsor_registry, build_test_client, sign_in


@pytest.fixture
def sponsor_client(settings: Settings, agent_answer: str):
    with build_test_client(
        settings, agent_answer=agent_answer, tool_registry=build_sponsor_registry()
    ) as test_client:
        yield test_client


@pytest.mark.governance
def test_sponsor_quote_masked_for_free_user(sponsor_client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(sponsor_client, settings, tier=Tier.FREE)
    resp = sponsor_client.get(
        "/api/v1/quote",
        params={"symbols": "2330", "market": "TW"},
        headers=headers,
    )
    assert resp.status_code == 200  # a teaser, never an auth error
    quote = resp.json()["quotes"][0]
    assert quote["locked"] is True
    assert quote["tool_name"] == "get_price_quote"


@pytest.mark.governance
def test_sponsor_quote_masked_for_anonymous(sponsor_client: TestClient) -> None:
    resp = sponsor_client.get("/api/v1/quote", params={"symbols": "2330", "market": "TW"})
    assert resp.status_code == 200
    assert resp.json()["quotes"][0]["locked"] is True


@pytest.mark.governance
def test_sponsor_quote_unmasked_for_premium_user(
    sponsor_client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(sponsor_client, settings, tier=Tier.PREMIUM)
    resp = sponsor_client.get(
        "/api/v1/quote",
        params={"symbols": "2330", "market": "TW"},
        headers=headers,
    )
    assert resp.status_code == 200
    quote = resp.json()["quotes"][0]
    assert "locked" not in quote
    assert quote["close"] == 101.0
