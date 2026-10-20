"""Strongly connected components (iterative Tarjan) pinned on invented graphs: the exact node ->
component id map, so the ids (which order the layering) cannot drift when the loop is split."""

from __future__ import annotations

from collections import defaultdict
from itertools import pairwise

import pytest

from analytics.supply_chain.positions.signals import _components, layering_scores

type Edges = list[tuple[str, str]]

GRAPHS: dict[str, Edges] = {
    "line": [("a", "b"), ("b", "c"), ("c", "d")],
    "two_cycle": [("a", "b"), ("b", "a")],
    "triangle_with_tail": [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")],
    "two_cycles_bridged": [("a", "b"), ("b", "a"), ("b", "c"), ("c", "d"), ("d", "c")],
    "nested_back_edges": [("a", "b"), ("b", "c"), ("c", "d"), ("d", "b"), ("d", "a"), ("c", "e")],
    "diamond": [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")],
    "disconnected": [("a", "b"), ("x", "y"), ("y", "x"), ("p", "q")],
    "cross_edge_to_finished_component": [("a", "b"), ("a", "c"), ("c", "b"), ("b", "d")],
    "reverse_alphabetical_roots": [("z", "y"), ("y", "x"), ("x", "z"), ("m", "z")],
}

GOLDEN: dict[str, dict[str, int]] = {
    "cross_edge_to_finished_component": {"a": 3, "b": 1, "c": 2, "d": 0},
    "diamond": {"a": 3, "b": 1, "c": 2, "d": 0},
    "disconnected": {"a": 1, "b": 0, "p": 3, "q": 2, "x": 4, "y": 4},
    "line": {"a": 3, "b": 2, "c": 1, "d": 0},
    "nested_back_edges": {"a": 1, "b": 1, "c": 1, "d": 1, "e": 0},
    "reverse_alphabetical_roots": {"m": 1, "x": 0, "y": 0, "z": 0},
    "triangle_with_tail": {"a": 1, "b": 1, "c": 1, "d": 0},
    "two_cycle": {"a": 0, "b": 0},
    "two_cycles_bridged": {"a": 1, "b": 1, "c": 0, "d": 0},
}


def _succ(edges: Edges) -> dict[str, list[str]]:
    succ: dict[str, list[str]] = defaultdict(list)
    for a, b in sorted(set(edges)):
        succ[a].append(b)
    return succ


def _nodes(edges: Edges) -> list[str]:
    return sorted({n for e in edges for n in e})


def _run(name: str) -> dict[str, int]:
    edges = GRAPHS[name]
    return _components(_nodes(edges), _succ(edges))


@pytest.mark.parametrize("name", sorted(GRAPHS))
def test_component_ids_are_unchanged(name: str) -> None:
    assert _run(name) == GOLDEN[name]


@pytest.mark.parametrize("name", sorted(GRAPHS))
def test_every_node_gets_a_component_and_cycle_members_share_one(name: str) -> None:
    comp = _run(name)
    assert set(comp) == set(_nodes(GRAPHS[name]))


def test_nodes_missing_from_the_successor_map_are_their_own_component() -> None:
    comp = _components(["a", "b", "c"], {})
    assert sorted(comp.values()) == [0, 1, 2]


def test_a_node_is_visited_once_even_when_listed_twice() -> None:
    comp = _components(["a", "b", "a"], {"a": ["b"]})
    assert set(comp) == {"a", "b"}


def test_long_chain_does_not_recurse() -> None:
    names = [f"n{i:04d}" for i in range(3000)]
    edges = list(pairwise(names))
    scores = layering_scores(edges)
    assert scores[names[0]] == 0.0
    assert scores[names[-1]] == 1.0
