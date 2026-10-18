"""Graph signals on invented graphs: layering depth, cycles, the two-edge rule, propagation."""

from __future__ import annotations

from analytics.supply_chain.positions.models import Position
from analytics.supply_chain.positions.signals import (
    ANCHOR_SCORE,
    MAX_ITERATIONS,
    chain_signals,
    layering_scores,
    position_for_score,
    propagate,
)

# a -> b -> c -> d : a supplies b, b supplies c, c supplies d.
LINE = [("a", "b"), ("b", "c"), ("c", "d")]


def test_layering_runs_from_supplier_side_to_customer_side() -> None:
    scores = layering_scores(LINE)
    assert scores["a"] == 0.0
    assert scores["d"] == 1.0
    assert 0 < scores["b"] < scores["c"] < 1


def test_layering_midpoint_of_a_three_node_path_is_half() -> None:
    assert layering_scores([("a", "b"), ("b", "c")])["b"] == 0.5


def test_layering_survives_cycles_and_self_loops() -> None:
    edges = [("a", "b"), ("b", "c"), ("c", "b"), ("c", "d"), ("d", "d")]
    scores = layering_scores(edges)
    assert scores["b"] == scores["c"]  # one strongly connected component, one layer
    assert scores["a"] < scores["b"] < scores["d"]


def test_nodes_with_fewer_than_two_edges_get_no_signal() -> None:
    signals = chain_signals([("a", "b"), ("b", "c")])
    assert set(signals) == {"b"}
    assert chain_signals([("a", "b")]) == {}
    assert chain_signals([]) == {}


def test_duplicate_edges_count_once() -> None:
    assert chain_signals([("a", "b"), ("a", "b")]) == {}


def test_layering_signal_labels_middle_of_path_midstream() -> None:
    signal = chain_signals([("a", "b"), ("b", "c")])["b"]
    assert (signal.position, signal.kind, signal.edge_count) == (
        Position.MIDSTREAM,
        "layering",
        2,
    )


def test_layering_signal_places_hub_ends() -> None:
    # u1,u2 -> hub -> d1,d2 ; the hub is the middle.  s -> u1 gives u1 two edges as well.
    edges = [("s", "u1"), ("u1", "hub"), ("u2", "hub"), ("hub", "d1"), ("hub", "d2"), ("d1", "e")]
    signals = chain_signals(edges)
    assert signals["u1"].position is Position.UPSTREAM
    assert signals["d1"].position is Position.DOWNSTREAM


def test_position_thresholds_and_margin_band() -> None:
    assert position_for_score(0.1) is Position.UPSTREAM
    assert position_for_score(0.5) is Position.MIDSTREAM
    assert position_for_score(0.9) is Position.DOWNSTREAM
    assert position_for_score(0.34) is None  # inside the band: no opinion
    assert position_for_score(0.66) is None
    assert position_for_score(0.31) is None
    assert position_for_score(0.29) is Position.UPSTREAM
    assert position_for_score(0.71) is Position.DOWNSTREAM


def test_propagation_converges_and_fixes_anchors() -> None:
    anchors = {"a": ANCHOR_SCORE[Position.UPSTREAM], "d": ANCHOR_SCORE[Position.DOWNSTREAM]}
    scores, iterations = propagate(LINE, anchors)
    assert iterations <= MAX_ITERATIONS
    assert scores["a"] == 0.0
    assert scores["d"] == 1.0
    assert scores["b"] < scores["c"]
    again, _ = propagate(LINE, anchors)
    assert again == scores  # deterministic


def test_propagation_converges_well_inside_the_cap_on_a_small_chain() -> None:
    _, iterations = propagate(LINE, {"a": 0.0, "d": 1.0})
    assert iterations < MAX_ITERATIONS


def test_propagation_leaves_unreachable_nodes_unknown() -> None:
    scores, _ = propagate([("a", "b"), ("x", "y")], {"a": 0.0})
    assert "x" not in scores and "y" not in scores
    assert scores["b"] == 0.25


def test_propagation_terminates_on_cycles() -> None:
    edges = [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")]
    scores, iterations = propagate(edges, {"d": 1.0})
    assert iterations <= MAX_ITERATIONS
    assert all(0.0 <= v <= 1.0 for v in scores.values())


def test_propagation_signal_is_preferred_and_leaves_the_anchor_out() -> None:
    # b sits between an upstream anchor and a downstream anchor.
    edges = [("a", "b"), ("b", "c")]
    anchors = {"a": Position.UPSTREAM, "c": Position.DOWNSTREAM}
    signal = chain_signals(edges, anchors)["b"]
    assert (signal.kind, signal.position) == ("propagation", Position.MIDSTREAM)


def test_anchor_node_is_judged_without_its_own_anchor() -> None:
    # b is anchored downstream, but its neighbours (upstream a, midstream c) say otherwise.
    edges = [("a", "b"), ("b", "c")]
    anchors = {"a": Position.UPSTREAM, "b": Position.DOWNSTREAM, "c": Position.DOWNSTREAM}
    signal = chain_signals(edges, anchors)["b"]
    assert signal.kind == "propagation"
    assert signal.position is Position.MIDSTREAM


def test_a_lone_anchor_cannot_confirm_itself() -> None:
    edges = [("a", "b"), ("b", "c")]
    signal = chain_signals(edges, {"b": Position.DOWNSTREAM})["b"]
    assert signal.kind == "layering"
