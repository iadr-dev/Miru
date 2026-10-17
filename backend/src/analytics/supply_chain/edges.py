"""The flat edge shape the API and the `get_supply_chain_edges` tool return for one company.

Additive over the legacy edge list: `confidence` stays (the old name for `grade`), `market` stays
(the supplier's market), and each edge gains per-endpoint markets, product, dates and sources.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from analytics.supply_chain.domain.models import (
    Grade,
    Kind,
    MarketCode,
    Origin,
    Relation,
    Status,
    Trust,
)

_GRADE_RANK = {Grade.HIGH: 0, Grade.MEDIUM: 1, Grade.LOW: 2}


class EdgeSourceLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    publisher: str
    url: str
    published_on: str | None = None


class ScEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_symbol: str
    to_symbol: str
    market: MarketCode
    from_market: MarketCode
    to_market: MarketCode
    edge_type: Kind
    confidence: Grade
    grade: Grade
    status: Status
    revenue_pct: float | None = None
    source: Origin
    from_name: str = ""
    to_name: str = ""
    product: str | None = None
    as_of: str | None = None
    last_verified_at: str | None = None
    sources: tuple[EdgeSourceLink, ...] = ()


def to_edge(relation: Relation) -> ScEdge:
    seen: set[str] = set()
    links: list[EdgeSourceLink] = []
    for e in relation.evidence:
        if not e.supports or e.trust is Trust.REJECTED or e.url in seen:
            continue
        seen.add(e.url)
        links.append(
            EdgeSourceLink(
                publisher=e.publisher,
                url=e.url,
                published_on=e.published_on.isoformat() if e.published_on else None,
            )
        )
    verified = relation.last_verified_at
    return ScEdge(
        from_symbol=relation.supplier.symbol,
        to_symbol=relation.customer.symbol,
        market=relation.supplier.market,
        from_market=relation.supplier.market,
        to_market=relation.customer.market,
        edge_type=relation.kind,
        confidence=relation.grade,
        grade=relation.grade,
        status=relation.status,
        revenue_pct=relation.revenue_pct,
        source=relation.origin,
        from_name=relation.supplier.name,
        to_name=relation.customer.name,
        product=relation.product,
        as_of=relation.as_of.isoformat() if relation.as_of else None,
        last_verified_at=verified.isoformat() if verified else None,
        sources=tuple(links),
    )


def to_edges(relations: Iterable[Relation]) -> tuple[ScEdge, ...]:
    edges = [to_edge(r) for r in relations]
    edges.sort(key=lambda e: (_GRADE_RANK[e.grade], e.from_symbol, e.to_symbol, e.product or ""))
    return tuple(edges)


__all__ = ["EdgeSourceLink", "ScEdge", "to_edge", "to_edges"]
