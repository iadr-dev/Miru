"""`GET /symbols/{symbol}/entity-graph` and the concept-theme additions to the theme routes,
through the real routes against the concept tables. Synthetic data only."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from analytics.supply_chain.domain.concepts import (
    ConceptMember,
    ConceptOrigin,
    ConceptPosition,
    ConceptTheme,
    Entity,
    EntityMention,
    EntityType,
    MentionSection,
)
from analytics.supply_chain.domain.models import Endpoint, Grade, Kind, Origin, Relation, Status
from analytics.supply_chain.store.concept_store import SqlAlchemyConceptStore
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from analytics.themes.models import ThemeKind, ThemeMember, ThemeSource
from analytics.themes.store import SqlAlchemyThemeStore
from config.settings import Settings
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.api.conftest import build_test_client
from tests.tools.conftest import make_router
from tools import build_default_tool_registry

ALPHA = Endpoint(symbol="9001", market="TW", name="Alpha")
BETA = Endpoint(symbol="9002", market="TW", name="Beta")


def _mention(symbol: str, entity: str, count: int) -> EntityMention:
    return EntityMention(
        symbol=symbol, entity_id=entity, section=MentionSection.BUSINESS, count=count
    )


def _seed_sync(session_factory: async_sessionmaker) -> None:
    engine = create_async_engine(session_factory.kw["bind"].url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _run() -> None:
        async with factory() as session:
            concepts = SqlAlchemyConceptStore(session)
            await concepts.replace_entities(
                [
                    Entity(id="Widget Tech", type=EntityType.TECHNOLOGY),
                    Entity(id="Widget App", type=EntityType.APPLICATION),
                ],
                [
                    _mention("9001", "Widget Tech", 4),
                    _mention("9001", "Widget App", 1),
                    _mention("9002", "Widget Tech", 2),
                ],
            )
            await concepts.replace_concept_themes(
                [
                    ConceptTheme(
                        key="wave",
                        name_zh="浪潮",
                        description="An invented theme",
                        related=("other",),
                        members=(
                            ConceptMember(
                                symbol="9001",
                                market="TW",
                                name="Alpha",
                                position=ConceptPosition.UPSTREAM,
                                origin=ConceptOrigin.SEEDED,
                            ),
                            ConceptMember(
                                symbol="9002",
                                market="TW",
                                name="Beta",
                                position=ConceptPosition.RELATED,
                                origin=ConceptOrigin.CURATED,
                            ),
                        ),
                    ),
                    ConceptTheme(key="other", name_zh="其他"),
                ]
            )
            themes = SqlAlchemyThemeStore(session)
            await themes.upsert_theme(
                key="industry-x",
                name_zh="產業",
                kind=ThemeKind.INDUSTRY,
                source=ThemeSource.INDUSTRY_CHAIN,
            )
            await themes.replace_theme_members(
                theme_key="industry-x",
                source=ThemeSource.INDUSTRY_CHAIN,
                members=[
                    ThemeMember(
                        symbol="9001",
                        market="TW",
                        sub_industry="x",
                        source=ThemeSource.INDUSTRY_CHAIN,
                    )
                ],
            )
            await SqlAlchemySupplyChainStore(session).upsert_relation(
                Relation(
                    supplier=ALPHA,
                    customer=BETA,
                    kind=Kind.SUPPLIES,
                    status=Status.VERIFIED,
                    grade=Grade.HIGH,
                    origin=Origin.MANUAL,
                    as_of=datetime.now(UTC).date(),
                )
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


def test_entity_graph_route_shape_is_public(settings: Settings, agent_answer: str) -> None:
    resp = _client(settings, agent_answer).get("/api/v1/symbols/9001/entity-graph")
    assert resp.status_code == 200  # anonymous caller
    body = resp.json()
    assert set(body) == {"symbol", "market", "nodes", "links"}
    assert (body["symbol"], body["market"]) == ("9001", "TW")
    nodes = {n["id"]: n for n in body["nodes"]}
    assert set(nodes) == {"co:TW:9001", "co:TW:9002", "ent:Widget Tech", "ent:Widget App"}
    assert nodes["co:TW:9001"]["focus"] is True
    assert nodes["ent:Widget Tech"] == {
        "id": "ent:Widget Tech",
        "label": "Widget Tech",
        "type": "technology",
        "symbol": None,
        "market": None,
        "weight": 4,
        "focus": False,
    }
    kinds = {(x["source"], x["target"], x["kind"]) for x in body["links"]}
    assert ("co:TW:9001", "ent:Widget Tech", "mention") in kinds
    assert ("co:TW:9002", "ent:Widget Tech", "mention") in kinds
    assert ("co:TW:9001", "co:TW:9002", "cooccurrence") in kinds
    assert ("co:TW:9001", "co:TW:9002", "supply") in kinds  # supplier -> customer


def test_entity_graph_params_narrow_and_validate(settings: Settings, agent_answer: str) -> None:
    client = _client(settings, agent_answer)
    narrow = client.get(
        "/api/v1/symbols/9001/entity-graph", params={"min_weight": 2, "limit": 5}
    ).json()
    assert {n["id"] for n in narrow["nodes"]} == {"co:TW:9001", "co:TW:9002", "ent:Widget Tech"}
    assert client.get("/api/v1/symbols/9001/entity-graph", params={"limit": 95}).status_code == 422
    assert client.get("/api/v1/symbols/9001/entity-graph", params={"limit": 0}).status_code == 422
    assert (
        client.get("/api/v1/symbols/9001/entity-graph", params={"min_weight": 0}).status_code == 422
    )
    assert (
        client.get("/api/v1/symbols/9001/entity-graph", params={"market": "JP"}).status_code == 422
    )


def test_unknown_company_gets_only_itself(settings: Settings, agent_answer: str) -> None:
    client = _client(settings, agent_answer)
    body = client.get("/api/v1/symbols/9999/entity-graph").json()
    assert [n["id"] for n in body["nodes"]] == ["co:TW:9999"]
    assert body["links"] == []
    us = client.get("/api/v1/symbols/ZZZA/entity-graph", params={"market": "US"}).json()
    assert (us["market"], len(us["nodes"])) == ("US", 1)


def test_theme_list_carries_description_and_related_keys(
    settings: Settings, agent_answer: str
) -> None:
    client = _client(settings, agent_answer)
    body = client.get("/api/v1/themes", params={"kind": "concept"}).json()
    by_key = {t["key"]: t for t in body["themes"]}
    assert set(by_key) == {"wave", "other"}
    assert by_key["wave"]["description"] == "An invented theme"
    assert by_key["wave"]["related_keys"] == ["other"]
    assert by_key["wave"]["member_count"] == 2
    industry = client.get("/api/v1/themes", params={"kind": "industry"}).json()["themes"]
    assert [(t["key"], t["description"], t["related_keys"]) for t in industry] == [
        ("industry-x", "", [])
    ]


def test_concept_theme_members_carry_a_position(settings: Settings, agent_answer: str) -> None:
    client = _client(settings, agent_answer)
    concept = client.get("/api/v1/themes/wave").json()
    assert concept["found"] is True
    assert (concept["description"], concept["related_keys"]) == ("An invented theme", ["other"])
    assert {m["symbol"]: (m["position"], m["sub_industry"]) for m in concept["members"]} == {
        "9001": ("upstream", "上游"),
        "9002": ("related", "相關公司"),
    }
    industry = client.get("/api/v1/themes/industry-x").json()
    assert [m["position"] for m in industry["members"]] == [None]
    assert industry["description"] == ""
    assert industry["related_keys"] == []
