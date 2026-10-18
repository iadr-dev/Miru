"""`GET /symbols/{symbol}/chain-positions` through the real route against `sc_positions`.
Synthetic data only."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from analytics.supply_chain.positions.models import (
    ChainPosition,
    Method,
    Position,
    Reason,
    ReasonCode,
)
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from config.settings import Settings
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.api.conftest import build_test_client
from tests.tools.conftest import make_router
from tools import build_default_tool_registry

COMPUTED = datetime(2026, 10, 17, 6, 30, tzinfo=UTC)


def _seed_sync(session_factory: async_sessionmaker) -> None:
    engine = create_async_engine(session_factory.kw["bind"].url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _run() -> None:
        async with factory() as session:
            await SqlAlchemySupplyChainStore(session).replace_positions(
                [
                    ChainPosition(
                        symbol="9001",
                        chain_key="chain-b",
                        chain_name="Chain B",
                        position=Position.DOWNSTREAM,
                        method=Method.PRIOR,
                        confidence=0.35,
                        reasons=(Reason(code=ReasonCode.PRIOR_KEYWORD, detail="by keyword"),),
                    ),
                    ChainPosition(
                        symbol="9001",
                        chain_key="chain-a",
                        chain_name="Chain A",
                        position=Position.MIDSTREAM,
                        is_primary=True,
                        method=Method.EVIDENCE,
                        confidence=0.95,
                        reasons=(
                            Reason(code=ReasonCode.AGREE_PRIOR_GRAPH, detail="both"),
                            Reason(code=ReasonCode.GRAPH_LAYERING),
                        ),
                    ),
                ],
                computed_at=COMPUTED,
            )
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
    client = build_test_client(settings, agent_answer=agent_answer)
    state = client.app.state.app_state
    router, _ = make_router(
        Symbol(id="9001", market=Market.TW), dict.fromkeys(Capability, _stub_result())
    )
    registry = build_default_tool_registry(router, session_factory=state.session_factory)
    object.__setattr__(state, "tool_registry", registry)
    _seed_sync(state.session_factory)
    return client


def test_route_shape_is_public_and_primary_first(settings: Settings, agent_answer: str) -> None:
    resp = _client(settings, agent_answer).get(
        "/api/v1/symbols/9001/chain-positions", params={"market": "TW"}
    )
    assert resp.status_code == 200  # anonymous caller
    body = resp.json()
    assert set(body) == {"symbol", "market", "positions"}
    assert (body["symbol"], body["market"]) == ("9001", "TW")
    first, second = body["positions"]
    assert set(first) == {
        "chain_key",
        "chain_name",
        "position",
        "is_primary",
        "method",
        "confidence",
        "reasons",
        "computed_at",
    }
    assert (first["chain_key"], first["position"], first["method"], first["is_primary"]) == (
        "chain-a",
        "midstream",
        "evidence",
        True,
    )
    assert first["reasons"] == [
        {"code": "agree_prior_graph", "detail": "both"},
        {"code": "graph_layering", "detail": ""},
    ]
    assert first["computed_at"].startswith("2026-10-17T06:30")
    assert (second["chain_key"], second["is_primary"], second["method"]) == (
        "chain-b",
        False,
        "prior",
    )


def test_unclassified_company_returns_an_empty_list(settings: Settings, agent_answer: str) -> None:
    client = _client(settings, agent_answer)
    body = client.get("/api/v1/symbols/9999/chain-positions").json()
    assert body == {"symbol": "9999", "market": "TW", "positions": []}
    wrong_market = client.get("/api/v1/symbols/9001/chain-positions", params={"market": "US"})
    assert wrong_market.json()["positions"] == []
