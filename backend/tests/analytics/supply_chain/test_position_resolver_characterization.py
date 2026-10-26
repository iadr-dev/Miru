"""More `resolve_positions` behaviour: markets, anchors, empty input, output order."""

from __future__ import annotations

from analytics.supply_chain.positions.models import (
    ChainMember,
    ChainSpec,
    Method,
    Override,
    Prior,
    ReasonCode,
)
from analytics.supply_chain.positions.resolver import CURATED_CONFIDENCE, resolve_positions
from tests.analytics.supply_chain.test_position_resolver import CURATED, DOWN, MID, UP, _by, _chain


def test_no_chains_or_no_members_gives_no_rows() -> None:
    assert resolve_positions([], []) == []
    assert resolve_positions([_chain("c1", {})], [("1", "2")]) == []


def test_us_member_never_uses_the_graph_but_keeps_prior_and_override() -> None:
    chain = ChainSpec(
        chain_key="c1",
        chain_name="n",
        members=(
            ChainMember(symbol="1", sub_industry="x"),
            ChainMember(symbol="2", sub_industry="x"),
            ChainMember(symbol="3", sub_industry="x"),
            ChainMember(symbol="US1", market="US", sub_industry="cur"),
            ChainMember(symbol="US2", market="US", sub_industry="x"),
        ),
        tiers={"cur": Prior(position=DOWN, source=CURATED)},
    )
    override = Override(symbol="US2", market="US", chain_key="c1", position=UP, reason="r")
    rows = _by(resolve_positions([chain], [("1", "2"), ("2", "3"), ("US1", "2")], [override]))
    assert set(rows) == {("2", "c1"), ("US1", "c1"), ("US2", "c1")}
    assert rows[("US1", "c1")].method is Method.PRIOR
    assert rows[("US1", "c1")].confidence == CURATED_CONFIDENCE
    assert rows[("US2", "c1")].method is Method.OVERRIDE
    assert rows[("US2", "c1")].market == "US"


def test_override_is_matched_on_market_as_well_as_symbol() -> None:
    chain = ChainSpec(
        chain_key="c1",
        chain_name="n",
        members=(ChainMember(symbol="1", market="US", sub_industry="cur"),),
        tiers={"cur": Prior(position=MID, source=CURATED)},
    )
    tw_override = Override(symbol="1", market="TW", chain_key="c1", position=UP, reason="r")
    row = resolve_positions([chain], [], [tw_override])[0]
    assert (row.method, row.position) == (Method.PRIOR, MID)


def test_override_for_another_chain_does_not_apply() -> None:
    chain = _chain("c1", {"1": "s"}, {"s": (UP, CURATED)})
    other = Override(symbol="1", chain_key="c2", position=DOWN, reason="r")
    row = resolve_positions([chain], [], [other])[0]
    assert (row.method, row.position) == (Method.PRIOR, UP)


def test_curated_priors_anchor_their_neighbours() -> None:
    chain = _chain(
        "c1",
        {"1": "up", "2": "x", "3": "down"},
        {"up": (UP, CURATED), "down": (DOWN, CURATED)},
    )
    rows = _by(resolve_positions([chain], [("1", "2"), ("2", "3")]))
    assert [r.code for r in rows[("2", "c1")].reasons] == [ReasonCode.GRAPH_PROPAGATION]
    assert rows[("2", "c1")].position is MID
    assert rows[("1", "c1")].method is Method.PRIOR


def test_rows_are_sorted_by_symbol_market_and_chain_and_carry_chain_names() -> None:
    chains = [
        _chain("zz", {"2": "s", "1": "s"}, {"s": (UP, CURATED)}),
        _chain("aa", {"1": "s"}, {"s": (UP, CURATED)}),
    ]
    rows = resolve_positions(chains, [])
    assert [(r.symbol, r.chain_key) for r in rows] == [("1", "aa"), ("1", "zz"), ("2", "zz")]
    assert [r.chain_name for r in rows] == ["name-aa", "name-zz", "name-zz"]
    primary = {(r.symbol, r.chain_key) for r in rows if r.is_primary}
    assert primary == {("1", "zz"), ("2", "zz")}  # equal confidence: the larger chain wins


def test_edges_between_tw_members_only_count_for_the_chain_that_holds_both() -> None:
    wide = _chain("wide", {"1": "x", "2": "x", "3": "x"})
    narrow = _chain("narrow", {"1": "x", "2": "x"})
    rows = _by(resolve_positions([wide, narrow], [("1", "2"), ("2", "3")]))
    assert set(rows) == {("2", "wide")}
