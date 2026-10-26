"""Edge behaviours of `build_entity_graph` pinned on invented companies: ordering ties, caps,
peer->company-entity links, and supply links onto entity-mapped companies."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from analytics.supply_chain.domain.concepts import EntityType
from analytics.supply_chain.entity_graph import MAX_ENTITIES, EntityGraph, build_entity_graph
from analytics.supply_chain.store.concept_store import SqlAlchemyConceptStore
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from tests.analytics.supply_chain.builders import relation
from tests.analytics.supply_chain.test_entity_graph import (
    FOCUS,
    _ends,
    _entity,
    _mention,
)

ENTITIES = [
    _entity("Tie B"),
    _entity("Tie A"),
    _entity("Tie C"),
    _entity("Peer Co", EntityType.TW_COMPANY, "9102"),
    _entity("Focus Co", EntityType.TW_COMPANY, FOCUS),
]
MENTIONS = [
    _mention(FOCUS, "Tie B", 2),
    _mention(FOCUS, "Tie A", 2),
    _mention(FOCUS, "Tie C", 2),
    _mention(FOCUS, "Peer Co", 6),
    # peer 9102 mentions the entities it shares plus itself
    _mention("9102", "Tie A", 1),
    _mention("9102", "Tie B", 5),
    _mention("9102", "Peer Co", 3),
    # peer 9101 shares just one entity, with a lower total than 9102's
    _mention("9101", "Tie A", 9),
    # peer 9103 ties with 9101 on shared count and mention sum -> symbol breaks the tie
    _mention("9103", "Tie A", 9),
]


@pytest.fixture
async def seeded(session_factory: async_sessionmaker) -> async_sessionmaker:
    async with session_factory() as session:
        await SqlAlchemyConceptStore(session).replace_entities(ENTITIES, MENTIONS)
        sc = SqlAlchemySupplyChainStore(session)
        await sc.upsert_relation(relation(_ends("9102"), _ends(FOCUS)))
        await sc.upsert_relation(relation(_ends("9101"), _ends("9102")))
        await session.commit()
    return session_factory


async def _graph(factory: async_sessionmaker, **kwargs: int) -> EntityGraph:
    async with factory() as session:
        return await build_entity_graph(
            SqlAlchemyConceptStore(session),
            SqlAlchemySupplyChainStore(session),
            FOCUS,
            "TW",
            **kwargs,
        )


async def test_node_and_link_order_is_deterministic(seeded: async_sessionmaker) -> None:
    graph = await _graph(seeded)
    ids = [n.id for n in graph.nodes]
    # focus first, then peers (most shared, then mention sum, then symbol), then entities by
    # weight desc / id asc; a company entity folds into its peer node
    assert ids == [
        "co:TW:9001",
        "co:TW:9102",
        "co:TW:9101",
        "co:TW:9103",
        "ent:Tie A",
        "ent:Tie B",
        "ent:Tie C",
    ]
    assert [(x.kind, x.source, x.target) for x in graph.links] == [
        ("cooccurrence", "co:TW:9001", "co:TW:9102"),
        ("cooccurrence", "co:TW:9001", "co:TW:9101"),
        ("cooccurrence", "co:TW:9001", "co:TW:9103"),
        ("mention", "co:TW:9001", "co:TW:9102"),  # the company entity, weight = focus mentions
        ("mention", "co:TW:9001", "ent:Tie A"),
        ("mention", "co:TW:9001", "ent:Tie B"),
        ("mention", "co:TW:9001", "ent:Tie C"),
        ("mention", "co:TW:9102", "ent:Tie A"),
        ("mention", "co:TW:9102", "ent:Tie B"),
        ("mention", "co:TW:9101", "ent:Tie A"),
        ("mention", "co:TW:9103", "ent:Tie A"),
        ("supply", "co:TW:9101", "co:TW:9102"),
        ("supply", "co:TW:9102", "co:TW:9001"),
    ]


async def test_a_peer_never_links_to_itself(seeded: async_sessionmaker) -> None:
    graph = await _graph(seeded)
    assert all(x.source != x.target for x in graph.links)
    assert not any(x.source == "co:TW:9102" and x.target == "co:TW:9102" for x in graph.links)


async def test_limit_is_clamped_between_one_and_the_entity_cap(
    seeded: async_sessionmaker,
) -> None:
    zero = await _graph(seeded, limit=0)
    assert sum(n.id.startswith("ent:") for n in zero.nodes) + sum(
        n.id.startswith("co:") for n in zero.nodes
    ) == len(zero.nodes)
    # limit 0 behaves as 1: only the heaviest ranked entity (the company, 6 mentions) is shown
    assert [n.id for n in zero.nodes if n.id.startswith("ent:")] == []
    assert "co:TW:9102" in {n.id for n in zero.nodes}
    huge = await _graph(seeded, limit=10 * MAX_ENTITIES)
    assert {n.id for n in huge.nodes if n.id.startswith("ent:")} == {
        "ent:Tie A",
        "ent:Tie B",
        "ent:Tie C",
    }


async def test_non_tw_focus_has_no_neighbours(seeded: async_sessionmaker) -> None:
    async with seeded() as session:
        graph = await build_entity_graph(
            SqlAlchemyConceptStore(session), SqlAlchemySupplyChainStore(session), "ZZZZ", "US"
        )
    assert [n.id for n in graph.nodes] == ["co:US:ZZZZ"]
    assert graph.links == ()
