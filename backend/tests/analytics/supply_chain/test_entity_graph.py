"""The entity ego graph over synthetic mentions. Invented companies and entities only."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from analytics.supply_chain.domain.concepts import (
    Entity,
    EntityMention,
    EntityType,
    MentionSection,
)
from analytics.supply_chain.domain.models import Endpoint, Status
from analytics.supply_chain.entity_graph import (
    MAX_NODES,
    MAX_PEERS,
    EntityGraph,
    build_entity_graph,
    jaccard,
)
from analytics.supply_chain.store.concept_store import SqlAlchemyConceptStore
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from tests.analytics.supply_chain.builders import relation

FOCUS = "9001"


def _entity(
    id_: str, type_: EntityType = EntityType.TECHNOLOGY, symbol: str | None = None
) -> Entity:
    market = None if symbol is None else ("US" if symbol.isalpha() else "TW")
    return Entity(id=id_, type=type_, symbol=symbol, market=market)


def _mention(symbol: str, entity: str, count: int, section: str = "business") -> EntityMention:
    return EntityMention(
        symbol=symbol, entity_id=entity, section=MentionSection(section), count=count
    )


ENTITIES = [
    _entity("Widget Tech"),
    _entity("Widget Material", EntityType.MATERIAL),
    _entity("Widget App", EntityType.APPLICATION),
    _entity("Gamma Corp", EntityType.INTL_COMPANY, "ZZZA"),
    _entity("Beta Devices", EntityType.TW_COMPANY, "9002"),
    _entity("Alpha Parts", EntityType.TW_COMPANY, FOCUS),
    _entity("Lonely"),
]
MENTIONS = [
    _mention(FOCUS, "Widget Tech", 3),
    _mention(FOCUS, "Widget Tech", 2, "chain"),  # totals 5 across sections
    _mention(FOCUS, "Widget Material", 1),
    _mention(FOCUS, "Widget App", 2),
    _mention(FOCUS, "Gamma Corp", 3, "counterparty"),
    _mention(FOCUS, "Beta Devices", 2, "counterparty"),
    _mention(FOCUS, "Alpha Parts", 9),  # the company mentioning itself
    _mention("9002", "Widget Tech", 2),
    _mention("9002", "Widget Material", 1),
    _mention("9003", "Widget Tech", 1),
    _mention("9003", "Widget App", 4),
    _mention("9003", "Lonely", 1),
    _mention("9004", "Lonely", 1),
]


def _ends(symbol: str, market: str = "TW") -> Endpoint:
    return Endpoint(symbol=symbol, market=market, name="n")  # type: ignore[arg-type]


@pytest.fixture
async def seeded(session_factory: async_sessionmaker) -> async_sessionmaker:
    async with session_factory() as session:
        await SqlAlchemyConceptStore(session).replace_entities(ENTITIES, MENTIONS)
        sc = SqlAlchemySupplyChainStore(session)
        await sc.upsert_relation(relation(_ends(FOCUS), _ends("ZZZA", "US")))
        await sc.upsert_relation(relation(_ends("9002"), _ends(FOCUS)))
        await sc.upsert_relation(relation(_ends("9003"), _ends(FOCUS), status=Status.CANDIDATE))
        await sc.upsert_relation(relation(_ends("9099"), _ends(FOCUS)))  # 9099 is not shown
        await session.commit()
    return session_factory


async def _graph(
    factory: async_sessionmaker, symbol: str = FOCUS, market: str = "TW", **kwargs: int
) -> EntityGraph:
    async with factory() as session:
        return await build_entity_graph(
            SqlAlchemyConceptStore(session),
            SqlAlchemySupplyChainStore(session),
            symbol,
            market,  # type: ignore[arg-type]
            **kwargs,
        )


def _links(graph: EntityGraph, kind: str) -> dict[tuple[str, str], float]:
    return {(x.source, x.target): x.weight for x in graph.links if x.kind == kind}


def test_jaccard() -> None:
    assert jaccard(2, 5, 2) == 0.4
    assert jaccard(0, 0, 0) == 0.0
    assert jaccard(3, 3, 3) == 1.0


async def test_focus_entities_and_peers_with_weights(seeded: async_sessionmaker) -> None:
    graph = await _graph(seeded)
    nodes = {n.id: n for n in graph.nodes}
    focus = nodes["co:TW:9001"]
    assert focus.focus
    assert [n.id for n in graph.nodes if n.focus] == ["co:TW:9001"]
    assert focus.label == "Alpha Parts"
    assert nodes["ent:Widget Tech"].weight == 5  # summed across sections
    assert nodes["ent:Widget Tech"].type is EntityType.TECHNOLOGY
    assert "ent:Alpha Parts" not in nodes  # a company is not its own neighbour
    # peers share >= 1 shown entity; 9004 shares none
    assert {i for i in nodes if i.startswith("co:")} == {"co:TW:9001", "co:TW:9002", "co:TW:9003"}
    assert nodes["co:TW:9002"].weight == 2  # Widget Tech, Widget Material
    assert nodes["co:TW:9003"].weight == 2  # Widget Tech, Widget App


async def test_company_entities_map_onto_the_peer_node_instead_of_duplicating_it(
    seeded: async_sessionmaker,
) -> None:
    graph = await _graph(seeded)
    ids = {n.id for n in graph.nodes}
    assert "ent:Beta Devices" not in ids
    assert _links(graph, "mention")[("co:TW:9001", "co:TW:9002")] == 2.0
    gamma = next(n for n in graph.nodes if n.id == "ent:Gamma Corp")
    assert (gamma.symbol, gamma.market, gamma.type) == ("ZZZA", "US", EntityType.INTL_COMPANY)


async def test_links_by_kind(seeded: async_sessionmaker) -> None:
    graph = await _graph(seeded)
    mention = _links(graph, "mention")
    assert mention[("co:TW:9001", "ent:Widget Tech")] == 5.0
    assert mention[("co:TW:9002", "ent:Widget Tech")] == 2.0
    assert mention[("co:TW:9003", "ent:Widget App")] == 4.0
    assert ("co:TW:9003", "ent:Lonely") not in mention  # only the entities shown
    co = _links(graph, "cooccurrence")
    # Jaccard over the full entity sets: focus 5 (its own name excluded); 9002 has 2, shares 2;
    # 9003 has 3, shares 2
    assert co == {
        ("co:TW:9001", "co:TW:9002"): pytest.approx(0.4, abs=1e-4),
        ("co:TW:9001", "co:TW:9003"): pytest.approx(1 / 3, abs=1e-4),
    }


async def test_verified_supply_links_are_directed_and_only_between_shown_companies(
    seeded: async_sessionmaker,
) -> None:
    graph = await _graph(seeded)
    assert _links(graph, "supply") == {
        ("co:TW:9001", "ent:Gamma Corp"): 1.0,  # supplier -> customer, a US company entity
        ("co:TW:9002", "co:TW:9001"): 1.0,
    }  # the candidate 9003 -> 9001 and the unshown 9099 are absent


async def test_min_weight_and_limit_narrow_the_entities_and_peers(
    seeded: async_sessionmaker,
) -> None:
    heavy = await _graph(seeded, min_weight=3)
    assert {n.id for n in heavy.nodes if n.id.startswith("ent:")} == {
        "ent:Widget Tech",
        "ent:Gamma Corp",
    }
    top = await _graph(seeded, limit=1)
    assert [n.id for n in top.nodes if n.id.startswith("ent:")] == ["ent:Widget Tech"]
    assert {n.id for n in top.nodes if n.id.startswith("co:")} == {
        "co:TW:9001",
        "co:TW:9002",
        "co:TW:9003",
    }
    nothing = await _graph(seeded, min_weight=100)
    assert [n.id for n in nothing.nodes] == ["co:TW:9001"]
    assert nothing.links == ()


async def test_unknown_company_and_us_focus_return_just_the_focus(
    seeded: async_sessionmaker,
) -> None:
    unknown = await _graph(seeded, symbol="9777")
    assert [(n.id, n.label, n.weight) for n in unknown.nodes] == [("co:TW:9777", "9777", 0)]
    assert unknown.links == ()
    us = await _graph(seeded, symbol="ZZZA", market="US")
    assert [(n.id, n.label, n.type) for n in us.nodes] == [
        ("co:US:ZZZA", "Gamma Corp", EntityType.INTL_COMPANY)
    ]


async def test_node_cap_holds_for_a_dense_company(session_factory: async_sessionmaker) -> None:
    entities = [_entity(f"E{i:03d}") for i in range(200)]
    mentions = [_mention(FOCUS, e.id, 10 + (i % 7)) for i, e in enumerate(entities)]
    for peer in range(9100, 9140):  # 40 peers, each sharing a few of the entities
        mentions += [
            _mention(str(peer), entities[i].id, 1) for i in range(peer - 9100, peer - 9090)
        ]
    async with session_factory() as session:
        await SqlAlchemyConceptStore(session).replace_entities(entities, mentions)
        await session.commit()
    graph = await _graph(session_factory, limit=94)
    assert len(graph.nodes) <= MAX_NODES
    assert sum(n.id.startswith("co:") for n in graph.nodes) == MAX_PEERS + 1
    assert sum(n.id.startswith("ent:") for n in graph.nodes) == 94
    ids = {n.id for n in graph.nodes}
    assert all(x.source in ids and x.target in ids for x in graph.links)
    assert len(ids) == len(graph.nodes)


async def test_output_carries_only_ids_labels_types_and_counts(seeded: async_sessionmaker) -> None:
    dumped = (await _graph(seeded)).model_dump(mode="json")
    assert set(dumped) == {"symbol", "market", "nodes", "links"}
    for node in dumped["nodes"]:
        assert set(node) == {"id", "label", "type", "symbol", "market", "weight", "focus"}
    for link in dumped["links"]:
        assert set(link) == {"source", "target", "weight", "kind"}
