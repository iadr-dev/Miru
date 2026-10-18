"""The Pro-only supply-chain / concept-theme REST routes return a server-side preview below Pro
(anonymous, Free, Premium) and everything at Pro - never a 403. Each route is hit at every tier
through the real FastAPI app. Synthetic data only."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config.settings import Settings
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.api.conftest import build_test_client, enter_client, sign_in
from tests.tools.conftest import make_router
from tests.tools.test_theme_previews import _seed
from tools import build_default_tool_registry

TIERS = [None, Tier.FREE, Tier.PREMIUM, Tier.PRO]
LOCKED_PATHS = {
    "supply": "/api/v1/symbols/9001/supply-chain",
    "chain": "/api/v1/symbols/9001/chain-positions",
    "graph": "/api/v1/symbols/9001/entity-graph",
}


def _stub_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub caveat",
        payload={},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _client(settings: Settings, agent_answer: str, tier: Tier | None) -> TestClient:
    client = build_test_client(settings, agent_answer=agent_answer)
    state = client.app.state.app_state
    router, _ = make_router(
        Symbol(id="9001", market=Market.TW), dict.fromkeys(Capability, _stub_result())
    )
    registry = build_default_tool_registry(router, session_factory=state.session_factory)
    object.__setattr__(state, "tool_registry", registry)

    engine = create_async_engine(state.session_factory.kw["bind"].url)

    async def _run() -> None:
        await _seed(async_sessionmaker(engine, expire_on_commit=False))
        await engine.dispose()

    asyncio.run(_run())
    if tier is not None:
        enter_client(client).headers.update(sign_in(client, settings, tier=tier)[0])
    return client


@pytest.mark.parametrize("tier", TIERS)
def test_routes_preview_below_pro_and_never_403(
    settings: Settings, agent_answer: str, tier: Tier | None
) -> None:
    client = _client(settings, agent_answer, tier)
    full = tier is Tier.PRO

    supply = client.get(LOCKED_PATHS["supply"])
    chain = client.get(LOCKED_PATHS["chain"])
    graph = client.get(LOCKED_PATHS["graph"])
    assert (supply.status_code, chain.status_code, graph.status_code) == (200, 200, 200)

    s, c, g = supply.json(), chain.json(), graph.json()
    assert s["edge_count"] == (13 if full else 9)
    assert (s["locked"], s["sources_locked"]) == (not full, not full)
    assert (any(e["sources"] for e in s["edges"])) is full
    assert len(c["positions"]) == (2 if full else 1)
    assert c["locked"] is (not full)
    assert (any(p["reasons"] for p in c["positions"])) is full
    assert g["locked"] is (not full)
    assert (len(g["nodes"]) > 9) is full
    if not full:
        assert s["hidden_count"] == 4
        assert c["hidden_count"] == 1
        assert {link["kind"] for link in g["links"]} == {"mention"}
        assert "Pro plan" in s["upgrade_note"]


@pytest.mark.parametrize("tier", TIERS)
def test_theme_routes_by_tier(settings: Settings, agent_answer: str, tier: Tier | None) -> None:
    client = _client(settings, agent_answer, tier)
    full = tier is Tier.PRO

    listing = client.get("/api/v1/themes")
    assert listing.status_code == 200
    body = listing.json()
    assert {t["key"] for t in body["themes"]} == {"wave", "other", "industry-x"}
    assert body["concept_locked"] is (not full)
    concept_only = client.get("/api/v1/themes", params={"kind": "concept"}).json()
    assert {t["key"] for t in concept_only["themes"]} == {"wave", "other"}

    detail = client.get("/api/v1/themes/wave")
    assert detail.status_code == 200
    d = detail.json()
    assert d["related_keys"] == ["other"]
    assert len(d["members"]) == (9 if full else 7)
    assert (d["locked"], d["hidden_count"]) == ((False, 0) if full else (True, 2))

    industry = client.get("/api/v1/themes/industry-x").json()
    assert len(industry["members"]) == 6
    assert industry["locked"] is False

    mine = client.get("/api/v1/symbols/9001/themes").json()
    assert [t["theme_key"] for t in mine["themes"] if t["kind"] == "concept"] == ["wave"]
    assert mine["concept_locked"] is (not full)

    # The industry-chain tier endpoints stay fully free for everyone.
    assert client.get("/api/v1/themes/industry-x/tiers").status_code == 200


def test_withheld_content_is_not_in_the_body_anywhere(
    settings: Settings, agent_answer: str
) -> None:
    """A Free caller's raw response bodies carry none of the withheld peers or source links."""
    client = _client(settings, agent_answer, Tier.FREE)
    for path in (*LOCKED_PATHS.values(), "/api/v1/themes/wave"):
        text = client.get(path).text
        assert "example.com" not in text
    supply = client.get(LOCKED_PATHS["supply"]).text
    for withheld_peer in ("9010", "9011"):  # lowest-revenue customers
        assert f'"to_symbol":"{withheld_peer}"' not in supply.replace(" ", "")
    members = client.get("/api/v1/themes/wave").text
    for withheld in ("9103", "9104"):  # the 4th and 5th upstream members
        assert withheld not in members


def test_openapi_declares_the_preview_fields(settings: Settings, agent_answer: str) -> None:
    spec = build_test_client(settings, agent_answer=agent_answer).app.openapi()
    schemas = spec["components"]["schemas"]
    for name, fields in {
        "GetSupplyChainEdgesOutput": {"locked", "hidden_count", "sources_locked"},
        "GetChainPositionOutput": {"locked", "hidden_count"},
        "GetEntityGraphOutput": {"locked", "hidden_count"},
        "GetThemeMembersOutput": {"locked", "hidden_count"},
        "GetThemesOutput": {"concept_locked"},
        "GetSymbolThemesOutput": {"concept_locked"},
    }.items():
        assert fields <= set(schemas[name]["properties"]), name
