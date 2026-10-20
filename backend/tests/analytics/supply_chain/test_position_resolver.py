"""Resolver precedence, abstention and the primary-chain rule on invented chains."""

from __future__ import annotations

from analytics.supply_chain.positions.models import (
    ChainMember,
    ChainPosition,
    ChainSpec,
    Method,
    Override,
    Position,
    Prior,
    PriorSource,
    ReasonCode,
)
from analytics.supply_chain.positions.resolver import (
    AGREE_CURATED_CONFIDENCE,
    CURATED_CONFIDENCE,
    resolve_positions,
)

UP, MID, DOWN = Position.UPSTREAM, Position.MIDSTREAM, Position.DOWNSTREAM


def _chain(
    key: str,
    members: dict[str, str],
    tiers: dict[str, tuple[Position, PriorSource]] | None = None,
) -> ChainSpec:
    return ChainSpec(
        chain_key=key,
        chain_name=f"name-{key}",
        members=tuple(ChainMember(symbol=s, sub_industry=sub) for s, sub in members.items()),
        tiers={sub: Prior(position=p, source=src) for sub, (p, src) in (tiers or {}).items()},
    )


def _by(rows: list[ChainPosition]) -> dict[tuple[str, str], ChainPosition]:
    return {(r.symbol, r.chain_key): r for r in rows}


def _codes(row: ChainPosition) -> list[ReasonCode]:
    return [r.code for r in row.reasons]


CURATED = PriorSource.CURATED
HEURISTIC = PriorSource.HEURISTIC


def test_override_beats_everything() -> None:
    chain = _chain("c1", {"1": "s"}, {"s": (UP, CURATED)})
    override = Override(symbol="1", chain_key="c1", position=DOWN, reason="operator decision")
    row = resolve_positions([chain], [], [override])[0]
    assert (row.position, row.method, row.confidence) == (DOWN, Method.OVERRIDE, 1.0)
    # the operator's private note is never published
    assert [(r.code, r.detail) for r in row.reasons] == [(ReasonCode.OVERRIDE, "")]


def test_curated_prior_alone() -> None:
    row = resolve_positions([_chain("c1", {"1": "s"}, {"s": (UP, CURATED)})], [])[0]
    assert (row.position, row.method, row.confidence) == (UP, Method.PRIOR, CURATED_CONFIDENCE)
    assert _codes(row) == [ReasonCode.PRIOR_CURATED]


def test_heuristic_prior_alone_abstains() -> None:
    # Measured at chance on the labelled set, so a keyword guess is never published.
    assert resolve_positions([_chain("c1", {"1": "s"}, {"s": (MID, HEURISTIC)})], []) == []


def test_no_prior_and_no_graph_abstains() -> None:
    assert resolve_positions([_chain("c1", {"1": "unknown"})], []) == []


def test_member_without_a_tier_for_its_sub_industry_abstains_while_others_resolve() -> None:
    chain = _chain("c1", {"1": "s", "2": "other"}, {"s": (UP, CURATED)})
    assert [r.symbol for r in resolve_positions([chain], [])] == ["1"]


def test_graph_alone_decides_when_there_is_no_prior() -> None:
    chain = _chain("c1", {"1": "x", "2": "x", "3": "x"})
    rows = _by(resolve_positions([chain], [("1", "2"), ("2", "3")]))
    assert set(rows) == {("2", "c1")}  # only the middle node has two edges
    row = rows[("2", "c1")]
    assert (row.position, row.method) == (MID, Method.EVIDENCE)
    assert _codes(row) == [ReasonCode.GRAPH_LAYERING]
    assert 0.6 <= row.confidence <= 0.8


def test_prior_and_graph_agree_gives_high_confidence() -> None:
    chain = _chain("c1", {"1": "x", "2": "mid", "3": "x"}, {"mid": (MID, CURATED)})
    row = _by(resolve_positions([chain], [("1", "2"), ("2", "3")]))[("2", "c1")]
    assert (row.position, row.method, row.confidence) == (
        MID,
        Method.EVIDENCE,
        AGREE_CURATED_CONFIDENCE,
    )
    assert ReasonCode.AGREE_PRIOR_GRAPH in _codes(row)
    assert ReasonCode.CONFLICT_GRAPH_OVER_PRIOR not in _codes(row)


def test_curated_prior_is_kept_over_a_disagreeing_graph_and_says_so() -> None:
    chain = _chain("c1", {"1": "x", "2": "kept", "3": "x"}, {"kept": (DOWN, CURATED)})
    row = _by(resolve_positions([chain], [("1", "2"), ("2", "3")]))[("2", "c1")]
    assert (row.position, row.method, row.confidence) == (DOWN, Method.PRIOR, CURATED_CONFIDENCE)
    assert _codes(row) == [ReasonCode.PRIOR_CURATED, ReasonCode.CONFLICT_PRIOR_KEPT]


def test_graph_over_heuristic_prior_has_no_conflict_reason() -> None:
    chain = _chain("c1", {"1": "x", "2": "kw", "3": "x"}, {"kw": (DOWN, HEURISTIC)})
    row = _by(resolve_positions([chain], [("1", "2"), ("2", "3")]))[("2", "c1")]
    assert row.position is MID
    assert ReasonCode.CONFLICT_GRAPH_OVER_PRIOR not in _codes(row)


def test_override_anchors_propagation_for_its_neighbours() -> None:
    # 2 has two edges; its neighbours are overridden upstream and downstream.
    chain = _chain("c1", {"1": "x", "2": "x", "3": "x"})
    overrides = [
        Override(symbol="1", chain_key="c1", position=UP, reason="r"),
        Override(symbol="3", chain_key="c1", position=DOWN, reason="r"),
    ]
    rows = _by(resolve_positions([chain], [("1", "2"), ("2", "3")], overrides))
    assert _codes(rows[("2", "c1")]) == [ReasonCode.GRAPH_PROPAGATION]
    assert rows[("1", "c1")].method is Method.OVERRIDE


def test_heuristic_priors_are_not_anchors() -> None:
    chain = _chain("c1", {"1": "kw", "2": "x", "3": "kw"}, {"kw": (UP, HEURISTIC)})
    rows = _by(resolve_positions([chain], [("1", "2"), ("2", "3")]))
    assert _codes(rows[("2", "c1")]) == [ReasonCode.GRAPH_LAYERING]


def test_edges_outside_the_chain_are_ignored() -> None:
    chain = _chain("c1", {"1": "x", "2": "x"})
    assert resolve_positions([chain], [("1", "2"), ("2", "9")]) == []


def test_inputs_hash_is_stable_and_tracks_inputs() -> None:
    a = resolve_positions([_chain("c1", {"1": "s"}, {"s": (UP, CURATED)})], [])[0]
    b = resolve_positions([_chain("c1", {"1": "s"}, {"s": (UP, CURATED)})], [])[0]
    c = resolve_positions([_chain("c1", {"1": "s"}, {"s": (DOWN, CURATED)})], [])[0]
    assert a.inputs_hash == b.inputs_hash != c.inputs_hash


def test_exactly_one_primary_per_symbol_highest_confidence_first() -> None:
    strong = _chain("zz", {"1": "x", "2": "mid", "3": "x"}, {"mid": (MID, CURATED)})
    weak = _chain("aa", {"2": "mid"}, {"mid": (MID, CURATED)})
    rows = _by(resolve_positions([weak, strong], [("1", "2"), ("2", "3")]))
    # prior and graph agree in zz (higher confidence), so zz is the primary chain of symbol 2
    assert rows[("2", "zz")].is_primary
    assert not rows[("2", "aa")].is_primary


def test_primary_tie_goes_to_the_larger_chain_then_alphabetical() -> None:
    tiers = {"s": (UP, CURATED)}
    small = _chain("aa", {"1": "s"}, tiers)
    big = _chain("bb", {"1": "s", "2": "s", "3": "s"}, tiers)
    rows = _by(resolve_positions([small, big], []))
    assert rows[("1", "bb")].is_primary
    assert not rows[("1", "aa")].is_primary

    same_a = _chain("aa", {"1": "s"}, tiers)
    same_b = _chain("bb", {"1": "s"}, tiers)
    rows = _by(resolve_positions([same_b, same_a], []))
    assert rows[("1", "aa")].is_primary
    assert not rows[("1", "bb")].is_primary


def test_every_symbol_has_exactly_one_primary() -> None:
    tiers = {"s": (UP, CURATED)}
    chains = [_chain(k, {"1": "s", "2": "s"}, tiers) for k in ("a", "b", "c")]
    rows = resolve_positions(chains, [])
    for symbol in ("1", "2"):
        assert sum(r.is_primary for r in rows if r.symbol == symbol) == 1
