"""Graph signals on the verified `supplies` relations inside one chain. Pure and deterministic.

Edges are `(supplier, customer)` pairs between members of the same chain. A node needs at least
`MIN_EDGES` distinct incident edges to get any signal at all.

Layering (anchor-free). Strongly connected components are collapsed first, so a supply cycle
cannot loop forever: all nodes of a cycle share one layer. On the resulting DAG let `depth(v)` be
the longest path (in edges) from any node with no supplier, and `height(v)` the longest path to any
node with no customer. The layering score is `depth / (depth + height)`: 0 for a pure supplier side
(upstream-leaning), 1 for a pure customer side (downstream-leaning), 0.5 in the middle of a path.

Propagation (anchored). Anchors are fixed scores (upstream 0, midstream 0.5, downstream 1) taken
from operator overrides and curated priors. Every other node starts unknown; each iteration sets

    score(v) = mean( min(1, score(u) + STEP)  for every supplier u of v with a score,
                     max(0, score(u) - STEP)  for every customer u of v with a score )

using the previous iteration's scores (Jacobi update, anchors stay fixed, a node with no scored
neighbour stays unknown). It stops when no score moves by more than `TOLERANCE` or after
`MAX_ITERATIONS`; the update is a contraction towards the anchors, so small chains converge in a
handful of iterations. For an anchor node the score is computed with that node's own anchor
removed (leave-one-out), so a prior is never "confirmed" by itself.

A score maps to a position at `UPSTREAM_BELOW` / `DOWNSTREAM_ABOVE`; within `MARGIN` of either
threshold the graph has no opinion. Propagation is preferred when it has an opinion, else layering.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Collection, Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

from analytics.supply_chain.positions.models import Position

type Edge = tuple[str, str]

MIN_EDGES = 2
STEP = 0.25
MAX_ITERATIONS = 20
TOLERANCE = 1e-3
UPSTREAM_BELOW = 0.34
DOWNSTREAM_ABOVE = 0.66
MARGIN = 0.04

ANCHOR_SCORE: Mapping[Position, float] = {
    Position.UPSTREAM: 0.0,
    Position.MIDSTREAM: 0.5,
    Position.DOWNSTREAM: 1.0,
}


class GraphSignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    score: float
    position: Position
    kind: Literal["layering", "propagation"]
    edge_count: int


def position_for_score(score: float) -> Position | None:
    """The position a score maps to, or None inside a margin band around a threshold."""
    if abs(score - UPSTREAM_BELOW) < MARGIN or abs(score - DOWNSTREAM_ABOVE) < MARGIN:
        return None
    if score < UPSTREAM_BELOW:
        return Position.UPSTREAM
    if score > DOWNSTREAM_ABOVE:
        return Position.DOWNSTREAM
    return Position.MIDSTREAM


def clean_edges(edges: Iterable[Edge]) -> frozenset[Edge]:
    """Distinct edges without self-loops."""
    return frozenset((a, b) for a, b in edges if a != b)


def _components(nodes: list[str], succ: Mapping[str, list[str]]) -> dict[str, int]:
    """Strongly connected components (iterative Tarjan); node -> component id."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    comp: dict[str, int] = {}
    counter = 0
    next_comp = 0
    for root in nodes:
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child = work.pop()
            if child == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            children = succ.get(node, [])
            descended = False
            while child < len(children):
                nxt = children[child]
                child += 1
                if nxt not in index:
                    work.append((node, child))
                    work.append((nxt, 0))
                    descended = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if descended:
                continue
            if low[node] == index[node]:
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    comp[member] = next_comp
                    if member == node:
                        break
                next_comp += 1
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return comp


def _longest_paths(
    comp_edges: Mapping[int, set[int]], comp_count: int, *, reverse: bool
) -> dict[int, int]:
    """Longest path (edges) ending at each component, over the DAG (or its reverse)."""
    out_edges: dict[int, set[int]] = defaultdict(set)
    indegree = dict.fromkeys(range(comp_count), 0)
    for a, targets in comp_edges.items():
        for b in targets:
            src, dst = (b, a) if reverse else (a, b)
            if dst not in out_edges[src]:
                out_edges[src].add(dst)
                indegree[dst] += 1
    depth = dict.fromkeys(range(comp_count), 0)
    ready = deque(sorted(c for c, d in indegree.items() if d == 0))
    while ready:
        cur = ready.popleft()
        for nxt in sorted(out_edges.get(cur, ())):
            depth[nxt] = max(depth[nxt], depth[cur] + 1)
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
    return depth


def layering_scores(edges: Collection[Edge]) -> dict[str, float]:
    """Layering score (0 supplier side .. 1 customer side) for every node touching an edge."""
    edge_set = clean_edges(edges)
    nodes = sorted({n for e in edge_set for n in e})
    succ: dict[str, list[str]] = defaultdict(list)
    for a, b in sorted(edge_set):
        succ[a].append(b)
    comp = _components(nodes, succ)
    comp_edges: dict[int, set[int]] = defaultdict(set)
    for a, b in edge_set:
        if comp[a] != comp[b]:
            comp_edges[comp[a]].add(comp[b])
    comp_count = len(set(comp.values()))
    depth = _longest_paths(comp_edges, comp_count, reverse=False)
    height = _longest_paths(comp_edges, comp_count, reverse=True)
    scores: dict[str, float] = {}
    for node in nodes:
        d, h = depth[comp[node]], height[comp[node]]
        scores[node] = d / (d + h) if d + h else 0.5
    return scores


def propagate(
    edges: Collection[Edge], anchors: Mapping[str, float], *, exclude: str | None = None
) -> tuple[dict[str, float], int]:
    """Anchored propagation (see module docstring). Returns (scores, iterations used)."""
    edge_set = clean_edges(edges)
    suppliers: dict[str, list[str]] = defaultdict(list)
    customers: dict[str, list[str]] = defaultdict(list)
    for a, b in sorted(edge_set):
        customers[a].append(b)
        suppliers[b].append(a)
    nodes = sorted({n for e in edge_set for n in e})
    node_set = set(nodes)
    fixed = {n: s for n, s in anchors.items() if n != exclude and n in node_set}
    scores = dict(fixed)
    iterations = 0
    for iterations in range(1, MAX_ITERATIONS + 1):  # noqa: B007 - the count is returned
        updated = dict(fixed)
        for node in nodes:
            if node in fixed:
                continue
            estimates = [min(1.0, scores[u] + STEP) for u in suppliers[node] if u in scores]
            estimates += [max(0.0, scores[u] - STEP) for u in customers[node] if u in scores]
            if estimates:
                updated[node] = sum(estimates) / len(estimates)
        moved = max((abs(updated[n] - scores.get(n, -1.0)) for n in updated), default=0.0)
        scores = updated
        if moved <= TOLERANCE:
            break
    return scores, iterations


def chain_signals(
    edges: Collection[Edge], anchors: Mapping[str, Position] | None = None
) -> dict[str, GraphSignal]:
    """The graph opinion for every node with >= MIN_EDGES distinct incident edges (and an opinion
    outside the margin band). `anchors` are the trusted positions that seed propagation."""
    edge_set = clean_edges(edges)
    incident: dict[str, int] = defaultdict(int)
    for a, b in edge_set:
        incident[a] += 1
        incident[b] += 1
    eligible = sorted(n for n, k in incident.items() if k >= MIN_EDGES)
    if not eligible:
        return {}
    layering = layering_scores(edge_set)
    seeds = {n: ANCHOR_SCORE[p] for n, p in (anchors or {}).items() if n in incident}
    base, _ = propagate(edge_set, seeds)
    signals: dict[str, GraphSignal] = {}
    for node in eligible:
        if node in seeds:
            scores = propagate(edge_set, seeds, exclude=node)[0] if len(seeds) > 1 else {}
        else:
            scores = base
        propagated = position_for_score(scores[node]) if node in scores else None
        if propagated is not None:
            signals[node] = GraphSignal(
                score=scores[node],
                position=propagated,
                kind="propagation",
                edge_count=incident[node],
            )
            continue
        layered = position_for_score(layering[node])
        if layered is not None:
            signals[node] = GraphSignal(
                score=layering[node], position=layered, kind="layering", edge_count=incident[node]
            )
    return signals


__all__ = [
    "ANCHOR_SCORE",
    "MARGIN",
    "MAX_ITERATIONS",
    "MIN_EDGES",
    "Edge",
    "GraphSignal",
    "chain_signals",
    "clean_edges",
    "layering_scores",
    "position_for_score",
    "propagate",
]
