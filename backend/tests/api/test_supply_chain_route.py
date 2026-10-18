"""`GET /symbols/{symbol}/supply-chain` through the real route against the new relation tables:
the additive edge fields, the `market` parameter, and hidden candidate/stale rows.
Synthetic data only."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from analytics.supply_chain.domain.models import Status, Trust
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from config.settings import Settings
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.analytics.supply_chain.builders import ALPHA, BETA, GAMMA, evidence, relation
from tests.api.conftest import build_test_client, enter_client, sign_in
from tests.tools.conftest import make_router
from tools import build_default_tool_registry


def _seed_sync(session_factory: async_sessionmaker) -> None:
    engine = create_async_engine(session_factory.kw["bind"].url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _run() -> None:
        async with factory() as session:
            store = SqlAlchemySupplyChainStore(session)
            await store.upsert_relation(
                relation(ALPHA, BETA, product="widgets", evidence_rows=(evidence(Trust.PRIMARY),))
            )
            await store.upsert_relation(relation(GAMMA, BETA))
            await store.upsert_relation(relation(ALPHA, GAMMA, status=Status.CANDIDATE))
            await store.upsert_relation(relation(BETA, ALPHA, as_of=date(2019, 1, 1)))
            await session.commit()
        await engine.dispose()

    asyncio.run(_run())


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


def _client(settings: Settings, agent_answer: str) -> TestClient:
    """A Pro caller: the full view (below Pro the routes return a preview - see
    `test_pro_previews.py`)."""
    client = build_test_client(settings, agent_answer=agent_answer)
    enter_client(client).headers.update(sign_in(client, settings, tier=Tier.PRO)[0])
    state = client.app.state.app_state
    router, _ = make_router(
        Symbol(id="9001", market=Market.TW), dict.fromkeys(Capability, _stub_result())
    )
    registry = build_default_tool_registry(router, session_factory=state.session_factory)
    object.__setattr__(state, "tool_registry", registry)
    _seed_sync(state.session_factory)
    return client


def test_route_returns_published_edges_with_additive_fields(
    settings: Settings, agent_answer: str
) -> None:
    resp = _client(settings, agent_answer).get(
        "/api/v1/symbols/9001/supply-chain", params={"market": "TW"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["edge_count"] == 1  # the candidate and the stale row are hidden
    edge = body["edges"][0]
    assert (edge["from_symbol"], edge["to_symbol"], edge["product"]) == ("9001", "9002", "widgets")
    assert edge["confidence"] == edge["grade"] == "high"
    assert edge["status"] == "verified"
    assert edge["sources"][0]["publisher"] == "Example News"


def test_route_honors_market_param(settings: Settings, agent_answer: str) -> None:
    client = _client(settings, agent_answer)
    us = client.get("/api/v1/symbols/ZZZA/supply-chain", params={"market": "US"}).json()
    assert [(e["from_symbol"], e["from_market"], e["to_market"]) for e in us["edges"]] == [
        ("ZZZA", "US", "TW")
    ]
    wrong = client.get("/api/v1/symbols/ZZZA/supply-chain", params={"market": "TW"}).json()
    assert wrong["edges"] == []
