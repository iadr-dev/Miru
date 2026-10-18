"""Resolve each (company, chain) to a position, with a method, a confidence and reason codes.

Precedence (first match wins):
  1. operator override                                  method override,  confidence 1.0
  2. prior and graph agree                              method evidence,  0.95 (curated) / 0.85
  3. graph opinion (>= 2 verified incident edges)       method evidence,  0.60 + 0.05 per edge
     beyond the second, capped at 0.80; a disagreement with a curated prior adds
     `conflict_graph_over_prior`
  4. curated prior (the tier map)                       method prior,     0.80
  5. heuristic prior (keyword rule)                     method prior,     0.35
  6. nothing -> abstain (no row; the UI shows 尚未分類)

`is_primary`: per company, the chain whose result has the highest confidence; ties go to the
chain with more members, then the alphabetically first key. Exactly one primary per company.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping

from analytics.supply_chain.domain.models import MarketCode
from analytics.supply_chain.positions.models import (
    ChainPosition,
    ChainSpec,
    Method,
    Override,
    Position,
    Prior,
    PriorSource,
    Reason,
    ReasonCode,
)
from analytics.supply_chain.positions.signals import Edge, GraphSignal, chain_signals

OVERRIDE_CONFIDENCE = 1.0
CURATED_CONFIDENCE = 0.8
HEURISTIC_CONFIDENCE = 0.35
AGREE_CURATED_CONFIDENCE = 0.95
AGREE_HEURISTIC_CONFIDENCE = 0.85
GRAPH_BASE_CONFIDENCE = 0.6
GRAPH_PER_EDGE = 0.05
GRAPH_MAX_CONFIDENCE = 0.8
ANCHOR_MIN_CONFIDENCE = 0.8
_EXTRA_EDGE_CAP = 4

type Key = tuple[str, MarketCode, str]


def _prior_confidence(prior: Prior) -> float:
    return CURATED_CONFIDENCE if prior.source is PriorSource.CURATED else HEURISTIC_CONFIDENCE


def _graph_reason(signal: GraphSignal) -> Reason:
    code = (
        ReasonCode.GRAPH_PROPAGATION if signal.kind == "propagation" else ReasonCode.GRAPH_LAYERING
    )
    return Reason(
        code=code,
        detail=f"{signal.position.value} (score {signal.score:.2f}, {signal.edge_count} edges)",
    )


def _prior_reason(prior: Prior, chain_key: str) -> Reason:
    code = (
        ReasonCode.PRIOR_CURATED
        if prior.source is PriorSource.CURATED
        else ReasonCode.PRIOR_KEYWORD
    )
    return Reason(code=code, detail=f"{prior.position.value} ({chain_key} sub-industry tier)")


def _inputs_hash(override: Override | None, prior: Prior | None, graph: GraphSignal | None) -> str:
    payload = {
        "override": override.position.value if override else None,
        "prior": [prior.position.value, prior.source.value] if prior else None,
        "graph": [graph.position.value, graph.kind, round(graph.score, 3), graph.edge_count]
        if graph
        else None,
    }
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _decide(
    chain_key: str, override: Override | None, prior: Prior | None, graph: GraphSignal | None
) -> tuple[Position, Method, float, tuple[Reason, ...]] | None:
    if override is not None:
        # The operator's free-text reason stays in the private overrides file: the stored reasons
        # are served to every user, so they carry the code only.
        reason = Reason(code=ReasonCode.OVERRIDE)
        return override.position, Method.OVERRIDE, OVERRIDE_CONFIDENCE, (reason,)
    if graph is not None:
        graph_reason = _graph_reason(graph)
        curated = prior is not None and prior.source is PriorSource.CURATED
        if prior is not None and prior.position is graph.position:
            confidence = AGREE_CURATED_CONFIDENCE if curated else AGREE_HEURISTIC_CONFIDENCE
            agree = Reason(
                code=ReasonCode.AGREE_PRIOR_GRAPH,
                detail=f"prior and graph both say {graph.position.value}",
            )
            return (
                graph.position,
                Method.EVIDENCE,
                confidence,
                (agree, _prior_reason(prior, chain_key), graph_reason),
            )
        extra = min(graph.edge_count - 2, _EXTRA_EDGE_CAP)
        confidence = min(GRAPH_BASE_CONFIDENCE + GRAPH_PER_EDGE * extra, GRAPH_MAX_CONFIDENCE)
        reasons = [graph_reason]
        if prior is not None and curated:
            reasons.append(
                Reason(
                    code=ReasonCode.CONFLICT_GRAPH_OVER_PRIOR,
                    detail=f"graph says {graph.position.value}, curated prior says "
                    f"{prior.position.value}",
                )
            )
        return graph.position, Method.EVIDENCE, confidence, tuple(reasons)
    if prior is not None:
        return (
            prior.position,
            Method.PRIOR,
            _prior_confidence(prior),
            (_prior_reason(prior, chain_key),),
        )
    return None


def resolve_positions(
    chains: Iterable[ChainSpec],
    edges: Collection[Edge],
    overrides: Iterable[Override] = (),
) -> list[ChainPosition]:
    """Positions for every member of every chain that has any basis; abstentions get no row.

    `edges` are verified `(supplier, customer)` pairs between Taiwan companies; each chain uses
    those whose two ends are both members of it."""
    override_by_key: dict[Key, Override] = {(o.symbol, o.market, o.chain_key): o for o in overrides}
    rows: list[ChainPosition] = []
    chain_sizes: dict[str, int] = {}
    for chain in chains:
        members = {m.symbol: m for m in chain.members}
        chain_sizes[chain.chain_key] = len(members)
        tw_symbols = {s for s, m in members.items() if m.market == "TW"}
        chain_edges = [(a, b) for a, b in edges if a in tw_symbols and b in tw_symbols]

        priors = {s: chain.tiers.get(m.sub_industry) for s, m in members.items()}

        anchors: dict[str, Position] = {}
        for symbol in sorted(members):
            member = members[symbol]
            override = override_by_key.get((symbol, member.market, chain.chain_key))
            prior = priors[symbol]
            if override is not None:
                anchors[symbol] = override.position
            elif prior is not None and _prior_confidence(prior) >= ANCHOR_MIN_CONFIDENCE:
                anchors[symbol] = prior.position
        signals = chain_signals(chain_edges, anchors)
        for symbol in sorted(members):
            member = members[symbol]
            override = override_by_key.get((symbol, member.market, chain.chain_key))
            prior = priors[symbol]
            graph = signals.get(symbol) if member.market == "TW" else None
            decided = _decide(chain.chain_key, override, prior, graph)
            if decided is None:
                continue
            position, method, confidence, reasons = decided
            rows.append(
                ChainPosition(
                    symbol=symbol,
                    market=member.market,
                    chain_key=chain.chain_key,
                    chain_name=chain.chain_name,
                    position=position,
                    method=method,
                    confidence=confidence,
                    reasons=reasons,
                    inputs_hash=_inputs_hash(override, prior, graph),
                )
            )
    return _mark_primary(rows, chain_sizes)


def _mark_primary(rows: list[ChainPosition], chain_sizes: Mapping[str, int]) -> list[ChainPosition]:
    by_symbol: dict[tuple[str, MarketCode], list[ChainPosition]] = defaultdict(list)
    for row in rows:
        by_symbol[(row.symbol, row.market)].append(row)
    primary: set[tuple[str, MarketCode, str]] = set()
    for (symbol, market), group in by_symbol.items():
        best = min(group, key=lambda r: (-r.confidence, -chain_sizes[r.chain_key], r.chain_key))
        primary.add((symbol, market, best.chain_key))
    out = [
        r.model_copy(update={"is_primary": (r.symbol, r.market, r.chain_key) in primary})
        for r in rows
    ]
    out.sort(key=lambda r: (r.symbol, r.market, r.chain_key))
    return out


__all__ = [
    "AGREE_CURATED_CONFIDENCE",
    "AGREE_HEURISTIC_CONFIDENCE",
    "CURATED_CONFIDENCE",
    "GRAPH_MAX_CONFIDENCE",
    "HEURISTIC_CONFIDENCE",
    "OVERRIDE_CONFIDENCE",
    "resolve_positions",
]
