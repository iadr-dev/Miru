"""The 關聯圖譜 ego graph of one company: the entities its reports mention, the other companies
that mention the same entities, and the verified supply relations among the companies shown.

Only ids, labels, types and counts are produced - never report text. Capped at `MAX_NODES`.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict

from analytics.supply_chain.domain.concepts import Entity, EntityType
from analytics.supply_chain.domain.models import MarketCode
from analytics.supply_chain.store.concept_protocol import ConceptStore
from analytics.supply_chain.store.protocol import SupplyChainStore

MAX_NODES = 120
MAX_PEERS = 25
MAX_ENTITIES = MAX_NODES - MAX_PEERS - 1
DEFAULT_LIMIT = 60

type LinkKind = Literal["mention", "cooccurrence", "supply"]


class GraphNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    type: EntityType
    symbol: str | None = None
    market: MarketCode | None = None
    weight: int
    focus: bool = False


class GraphLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    target: str
    weight: float
    kind: LinkKind


class EntityGraph(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: MarketCode
    nodes: tuple[GraphNode, ...]
    links: tuple[GraphLink, ...]


def company_node_id(symbol: str, market: str) -> str:
    return f"co:{market}:{symbol}"


def entity_node_id(entity_id: str) -> str:
    return f"ent:{entity_id}"


def jaccard(shared: int, left: int, right: int) -> float:
    """|A and B| / |A or B| for set sizes `left`, `right` and intersection `shared`."""
    union = left + right - shared
    return round(shared / union, 4) if union > 0 else 0.0


def _company_type(market: MarketCode, known: Entity | None) -> EntityType:
    if known is not None:
        return known.type
    return EntityType.TW_COMPANY if market == "TW" else EntityType.INTL_COMPANY


def _peer_node(peer: str, known: Entity | None, shared: int) -> GraphNode:
    return GraphNode(
        id=company_node_id(peer, "TW"),
        label=known.id if known else peer,
        type=_company_type("TW", known),
        symbol=peer,
        market="TW",
        weight=shared,
    )


def _entity_node(entity: Entity, count: int) -> GraphNode:
    return GraphNode(
        id=entity_node_id(entity.id),
        label=entity.id,
        type=entity.type,
        symbol=entity.symbol,
        market=entity.market,
        weight=count,
    )


def _existing_company_node(entity: Entity, nodes: dict[str, GraphNode]) -> str | None:
    """The id of the company node an entity stands for, when that company is already shown."""
    if entity.symbol is None or entity.market is None:
        return None
    node_id = company_node_id(entity.symbol, entity.market)
    return node_id if node_id in nodes else None


async def _supply_links(supply: SupplyChainStore, nodes: dict[str, GraphNode]) -> list[GraphLink]:
    by_company: dict[tuple[str, str], str] = {}
    for node in nodes.values():
        if node.symbol is not None and node.market is not None:
            by_company[(node.symbol, node.market)] = node.id
    return [
        GraphLink(
            source=by_company[supplier], target=by_company[customer], weight=1.0, kind="supply"
        )
        for supplier, customer in await supply.list_verified_supply_among(list(by_company))
    ]


async def build_entity_graph(  # noqa: PLR0913 - two stores, the focus, two query knobs
    concepts: ConceptStore,
    supply: SupplyChainStore,
    symbol: str,
    market: MarketCode,
    *,
    min_weight: int = 1,
    limit: int = DEFAULT_LIMIT,
) -> EntityGraph:
    """Focus company -> its mentioned entities -> peer companies sharing them.

    Entity weight is the company's total mention count; a peer's node weight is the number of
    shown entities it shares, and its focus link weight is the Jaccard overlap of the two
    companies' full entity sets (shared / union, 0..1). Supply links are directed
    supplier -> customer and exist only for verified, non-stale relations between shown companies.
    """
    cap = max(1, min(limit, MAX_ENTITIES))
    totals = await concepts.entity_totals(symbol) if market == "TW" else {}
    self_entities = await concepts.entities_by_symbol([symbol])
    own = self_entities.get((symbol, market))
    if own is not None:  # a company's mention of its own name says nothing about its neighbours
        totals = {eid: n for eid, n in totals.items() if eid != own.id}
    ranked = sorted(
        ((eid, n) for eid, n in totals.items() if n >= min_weight),
        key=lambda item: (-item[1], item[0]),
    )[:cap]
    shown = dict(ranked)

    peers_shared: dict[str, dict[str, int]] = defaultdict(dict)
    for peer, eid, count in await concepts.mentions_of_entities(list(shown)):
        if peer != symbol:
            peers_shared[peer][eid] = count
    peer_order = sorted(
        peers_shared, key=lambda p: (-len(peers_shared[p]), -sum(peers_shared[p].values()), p)
    )[:MAX_PEERS]
    peer_totals = await concepts.distinct_entity_counts(peer_order)

    entities = await concepts.get_entities(list(shown))
    named = await concepts.entities_by_symbol([symbol, *peer_order])

    focus_id = company_node_id(symbol, market)
    nodes: dict[str, GraphNode] = {
        focus_id: GraphNode(
            id=focus_id,
            label=own.id if own else symbol,
            type=_company_type(market, own),
            symbol=symbol,
            market=market,
            weight=sum(totals.values()),
            focus=True,
        )
    }
    links: list[GraphLink] = []
    for peer in peer_order:
        node = _peer_node(peer, named.get((peer, "TW")), len(peers_shared[peer]))
        nodes[node.id] = node
        links.append(
            GraphLink(
                source=focus_id,
                target=node.id,
                weight=jaccard(len(peers_shared[peer]), len(totals), peer_totals.get(peer, 0)),
                kind="cooccurrence",
            )
        )

    target_of: dict[str, str] = {}
    for eid, count in ranked:
        entity = entities.get(eid)
        if entity is None:
            continue
        mapped = _existing_company_node(entity, nodes)
        if mapped is None:
            mapped = entity_node_id(eid)
            nodes[mapped] = _entity_node(entity, count)
        target_of[eid] = mapped
        links.append(GraphLink(source=focus_id, target=mapped, weight=float(count), kind="mention"))
    for peer in peer_order:
        peer_id = company_node_id(peer, "TW")
        for eid, count in sorted(peers_shared[peer].items()):
            target = target_of.get(eid)
            if target is not None and target != peer_id:
                links.append(
                    GraphLink(source=peer_id, target=target, weight=float(count), kind="mention")
                )

    links.extend(await _supply_links(supply, nodes))
    return EntityGraph(
        symbol=symbol, market=market, nodes=tuple(nodes.values()), links=tuple(links)
    )


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_ENTITIES",
    "MAX_NODES",
    "MAX_PEERS",
    "EntityGraph",
    "GraphLink",
    "GraphNode",
    "build_entity_graph",
    "company_node_id",
    "entity_node_id",
    "jaccard",
]
