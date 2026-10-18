"""The Pro-only supply-chain / concept-theme tools return a server-side PREVIEW below Pro.

Pure preview functions are tested directly; the tier matrix (anonymous, Free, Premium, Pro) is
driven through the real registered tools + `dispatch_tool` against a seeded in-memory store.
Synthetic, invented data only."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from accounts.entitlements import FEATURE_MIN_TIER, tier_catalog
from analytics.supply_chain.domain.concepts import (
    ConceptMember,
    ConceptOrigin,
    ConceptPosition,
    ConceptTheme,
    Entity,
    EntityMention,
    EntityType,
    MentionSection,
)
from analytics.supply_chain.domain.models import Endpoint, Grade, Kind, Trust
from analytics.supply_chain.edges import ScEdge
from analytics.supply_chain.entity_graph import EntityGraph, GraphLink, GraphNode
from analytics.supply_chain.positions.models import (
    ChainPosition,
    Method,
    Position,
    Reason,
    ReasonCode,
)
from analytics.supply_chain.store.concept_store import SqlAlchemyConceptStore
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from analytics.themes.models import ThemeKind, ThemeMember, ThemeSource
from analytics.themes.store import SqlAlchemyThemeStore
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys
from schemas.enums import Tier, TLSPolicy
from schemas.feature_tiers import (
    PREVIEW_CHAIN_ROWS,
    PREVIEW_CONCEPT_MEMBERS_PER_POSITION,
    PREVIEW_ENTITY_NODES,
    PREVIEW_PEERS_PER_ROLE,
    access_for,
)
from tests.analytics.supply_chain.builders import evidence, relation
from tools.base import ToolContext, dispatch_tool
from tools.registry import ToolRegistry
from tools.theme_tools import (
    ChainPositionItem,
    ThemeMemberDetail,
    build_registration_table,
    preview_concept_members,
    preview_entity_graph,
    preview_positions,
    preview_supply_edges,
)

TIERS = [None, Tier.FREE, Tier.PREMIUM, Tier.PRO]


def _ctx(tier: Tier | None) -> ToolContext:
    if tier is None:  # anonymous
        return ToolContext(user_id=None, tls_policy=TLSPolicy.STRICT)
    return ToolContext(user_id="u1", tier=tier, tls_policy=TLSPolicy.STRICT)


def _full(tier: Tier | None) -> bool:
    return tier is Tier.PRO


# ---- the policy table -------------------------------------------------------------------------


def test_both_features_are_pro_rows_and_reach_the_pricing_projection() -> None:
    assert FEATURE_MIN_TIER["supply_chain"] is Tier.PRO
    assert FEATURE_MIN_TIER["concept_themes"] is Tier.PRO
    free, premium, pro = tier_catalog()
    for feature in ("supply_chain", "concept_themes"):
        assert feature not in free.features
        assert feature not in premium.features
        assert feature in pro.features


@pytest.mark.parametrize("feature", ["supply_chain", "concept_themes"])
def test_access_for_is_full_only_at_pro(feature: str) -> None:
    assert [access_for(feature, t).full for t in (Tier.FREE, Tier.PREMIUM, Tier.PRO)] == [
        False,
        False,
        True,
    ]


def test_an_unlisted_feature_is_unrestricted() -> None:
    assert access_for("not_a_feature", Tier.FREE).full is True


# ---- pure preview functions -------------------------------------------------------------------


def _edge(  # noqa: PLR0913 - a test builder
    frm: str,
    to: str,
    *,
    kind: Kind = Kind.SUPPLIES,
    grade: Grade = Grade.HIGH,
    revenue: float | None = None,
    name: str = "",
    with_source: bool = True,
) -> ScEdge:
    from analytics.supply_chain.edges import EdgeSourceLink  # noqa: PLC0415

    return ScEdge(
        from_symbol=frm,
        to_symbol=to,
        market="TW",
        from_market="TW",
        to_market="TW",
        edge_type=kind,
        confidence=grade,
        grade=grade,
        status="verified",
        revenue_pct=revenue,
        source="research",
        from_name=name if frm != "F" else "Focus",
        to_name=name if to != "F" else "Focus",
        sources=(EdgeSourceLink(publisher="Example", url="https://example.com/x"),)
        if with_source
        else (),
    )


def test_supply_preview_ranks_per_role_and_strips_sources() -> None:
    edges = [
        # customers of the focus (focus is the supplier)
        _edge("F", "C1", grade=Grade.LOW, revenue=50.0, name="c1"),
        _edge("F", "C2", grade=Grade.HIGH, revenue=None, name="c2"),
        _edge("F", "C3", grade=Grade.HIGH, revenue=10.0, name="c3"),
        _edge("F", "C4", grade=Grade.HIGH, revenue=30.0, name="c4"),
        _edge("F", "C5", grade=Grade.MEDIUM, revenue=90.0, name="c5"),
        # a supplier of the focus
        _edge("S1", "F", name="s1"),
        # competitors (either direction)
        _edge("F", "K1", kind=Kind.COMPETES, name="k1"),
        _edge("K2", "F", kind=Kind.COMPETES, name="k2"),
    ]
    kept, hidden, stripped = preview_supply_edges(edges, "F", "TW")
    customers = [e.to_symbol for e in kept if e.from_symbol == "F" and e.edge_type is Kind.SUPPLIES]
    # grade high>medium>low, then revenue desc with unknown last: C4(30), C3(10), C2(None)
    assert customers == ["C4", "C3", "C2"]
    assert [e.from_symbol for e in kept if e.to_symbol == "F" and e.edge_type is Kind.SUPPLIES] == [
        "S1"
    ]
    assert {e.edge_type for e in kept} == {Kind.SUPPLIES, Kind.COMPETES}
    assert len([e for e in kept if e.edge_type is Kind.COMPETES]) == 2
    assert all(e.sources == () for e in kept)
    assert stripped is True
    assert hidden == len(edges) - len(kept) == 2  # C1 and C5
    assert PREVIEW_PEERS_PER_ROLE == 3


def test_supply_preview_ties_break_by_name_and_keeps_one_edge_per_peer() -> None:
    edges = [
        _edge("F", "C2", name="bravo"),
        _edge("F", "C1", name="alpha"),
        _edge("F", "C1", name="alpha", grade=Grade.LOW),  # same peer, weaker edge
    ]
    kept, hidden, _ = preview_supply_edges(edges, "F", "TW")
    assert [e.to_symbol for e in kept] == ["C1", "C2"]
    assert [e.grade for e in kept] == [Grade.HIGH, Grade.HIGH]
    assert hidden == 1


def test_supply_preview_of_nothing_is_nothing() -> None:
    assert preview_supply_edges([], "F", "TW") == ((), 0, False)


def _item(key: str, *, primary: bool, reasons: int = 1) -> ChainPositionItem:
    return ChainPositionItem(
        chain_key=key,
        chain_name=key,
        position=Position.MIDSTREAM,
        is_primary=primary,
        method=Method.PRIOR,
        confidence=0.5,
        reasons=tuple(
            {"code": ReasonCode.PRIOR_KEYWORD, "detail": f"r{i}"} for i in range(reasons)
        ),  # type: ignore[arg-type]
    )


def test_positions_preview_is_the_primary_row_without_reasons() -> None:
    kept, hidden, stripped = preview_positions(
        [_item("a", primary=False), _item("b", primary=True), _item("c", primary=False)]
    )
    assert [i.chain_key for i in kept] == ["b"]
    assert kept[0].reasons == ()
    assert (hidden, stripped) == (2, True)
    assert len(kept) == PREVIEW_CHAIN_ROWS
    assert preview_positions([]) == ((), 0, False)


def _graph() -> EntityGraph:
    nodes = [
        GraphNode(
            id="co:TW:F",
            label="F",
            type=EntityType.TW_COMPANY,
            symbol="F",
            market="TW",
            weight=99,
            focus=True,
        ),
        GraphNode(
            id="co:TW:P1",
            label="P1",
            type=EntityType.TW_COMPANY,
            symbol="P1",
            market="TW",
            weight=3,
        ),
    ]
    links = [
        GraphLink(source="co:TW:F", target="co:TW:P1", weight=0.5, kind="cooccurrence"),
        GraphLink(source="co:TW:P1", target="co:TW:F", weight=1.0, kind="supply"),
    ]
    for i in range(12):
        nodes.append(
            GraphNode(id=f"ent:E{i:02d}", label=f"E{i:02d}", type=EntityType.TECHNOLOGY, weight=i)
        )
        links.append(
            GraphLink(source="co:TW:F", target=f"ent:E{i:02d}", weight=float(i), kind="mention")
        )
        links.append(
            GraphLink(source="co:TW:P1", target=f"ent:E{i:02d}", weight=1.0, kind="mention")
        )
    return EntityGraph(symbol="F", market="TW", nodes=tuple(nodes), links=tuple(links))


def test_entity_graph_preview_keeps_focus_and_top_entities_only() -> None:
    reduced, hidden = preview_entity_graph(_graph())
    ids = [n.id for n in reduced.nodes]
    assert ids[0] == "co:TW:F"
    assert ids[1:] == [f"ent:E{i:02d}" for i in range(11, 3, -1)]  # weights 11..4
    assert len(ids) - 1 == PREVIEW_ENTITY_NODES
    assert "co:TW:P1" not in ids
    assert hidden == 14 - 9
    assert {link.kind for link in reduced.links} == {"mention"}
    assert {link.source for link in reduced.links} == {"co:TW:F"}
    assert {link.target for link in reduced.links} <= set(ids)


def test_concept_member_preview_takes_first_three_per_position_in_order() -> None:
    def member(symbol: str, position: ConceptPosition) -> ThemeMemberDetail:
        return ThemeMemberDetail(
            symbol=symbol, market="TW", source=ThemeSource.CONCEPT_RESEARCH, position=position
        )

    members = [member(f"U{i}", ConceptPosition.UPSTREAM) for i in range(5)]
    members += [member("M0", ConceptPosition.MIDSTREAM)]
    members += [member(f"R{i}", ConceptPosition.RELATED) for i in range(4)]
    kept, hidden = preview_concept_members(members)
    assert [m.symbol for m in kept] == ["U0", "U1", "U2", "M0", "R0", "R1", "R2"]
    assert hidden == 3
    assert PREVIEW_CONCEPT_MEMBERS_PER_POSITION == 3


# ---- the tier matrix through the real tools ---------------------------------------------------

FOCUS = Endpoint(symbol="9001", market="TW", name="Alpha Parts")


def _peer(n: int) -> Endpoint:
    return Endpoint(symbol=f"90{n:02d}", market="TW", name=f"Peer {n:02d}")


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed(factory)
    yield factory
    await engine.dispose()


async def _seed(factory: async_sessionmaker) -> None:
    async with factory() as session:
        sc = SqlAlchemySupplyChainStore(session)
        for n in range(10, 15):  # five customers
            await sc.upsert_relation(
                relation(
                    FOCUS,
                    _peer(n),
                    revenue_pct=float(n),
                    evidence_rows=(evidence(Trust.PRIMARY, "Example IR", n),),
                )
            )
        for n in range(20, 24):  # four suppliers
            await sc.upsert_relation(relation(_peer(n), FOCUS, evidence_rows=(evidence(),)))
        for n in range(30, 34):  # four competitors
            await sc.upsert_relation(relation(FOCUS, _peer(n), kind=Kind.COMPETES))
        await sc.replace_positions(
            [
                ChainPosition(
                    symbol="9001",
                    chain_key="chain-b",
                    chain_name="Chain B",
                    position=Position.DOWNSTREAM,
                    method=Method.PRIOR,
                    confidence=0.35,
                    reasons=(Reason(code=ReasonCode.PRIOR_KEYWORD, detail="by keyword"),),
                ),
                ChainPosition(
                    symbol="9001",
                    chain_key="chain-a",
                    chain_name="Chain A",
                    position=Position.MIDSTREAM,
                    is_primary=True,
                    method=Method.EVIDENCE,
                    confidence=0.95,
                    reasons=(Reason(code=ReasonCode.GRAPH_LAYERING),),
                ),
            ],
            computed_at=datetime(2026, 10, 17, tzinfo=UTC),
        )
        concepts = SqlAlchemyConceptStore(session)
        entity_ids = [f"Ent {i:02d}" for i in range(12)]
        await concepts.replace_entities(
            [Entity(id=e, type=EntityType.TECHNOLOGY) for e in entity_ids],
            [
                EntityMention(
                    symbol=symbol, entity_id=e, section=MentionSection.BUSINESS, count=i + 1
                )
                for symbol in ("9001", "9010")
                for i, e in enumerate(entity_ids)
            ],
        )
        await concepts.replace_concept_themes(
            [
                ConceptTheme(
                    key="wave",
                    name_zh="浪潮",
                    description="An invented theme",
                    related=("other",),
                    members=(
                        *tuple(
                            ConceptMember(
                                symbol=f"91{i:02d}",
                                market="TW",
                                name=f"Member {i}",
                                position=ConceptPosition.UPSTREAM
                                if i < 5
                                else ConceptPosition.RELATED,
                                origin=ConceptOrigin.SEEDED,
                            )
                            for i in range(8)
                        ),
                        ConceptMember(
                            symbol="9001",
                            market="TW",
                            name="Alpha Parts",
                            position=ConceptPosition.MIDSTREAM,
                            origin=ConceptOrigin.SEEDED,
                        ),
                    ),
                ),
                ConceptTheme(key="other", name_zh="其他"),
            ]
        )
        themes = SqlAlchemyThemeStore(session)
        await themes.upsert_theme(
            key="industry-x",
            name_zh="產業",
            kind=ThemeKind.INDUSTRY,
            source=ThemeSource.INDUSTRY_CHAIN,
        )
        await themes.replace_theme_members(
            theme_key="industry-x",
            source=ThemeSource.INDUSTRY_CHAIN,
            members=[
                ThemeMember(
                    symbol=f"92{i:02d}",
                    market="TW",
                    sub_industry="x",
                    source=ThemeSource.INDUSTRY_CHAIN,
                )
                for i in range(6)
            ],
        )
        await session.commit()


async def _call(
    factory: async_sessionmaker, tool_name: str, tier: Tier | None, **args: object
) -> dict:
    registry = ToolRegistry()
    for tool, immediate in build_registration_table(factory):
        registry.register(tool, immediate=immediate)
    tool = registry.get_tool(tool_name)
    assert tool is not None
    result = await dispatch_tool(tool, tool.input_model.model_validate(args), _ctx(tier))
    return result.model_dump(mode="json")


@pytest.mark.parametrize("tier", TIERS)
async def test_supply_chain_edges_by_tier(session_factory: async_sessionmaker, tier) -> None:
    data = await _call(session_factory, "get_supply_chain_edges", tier, id="9001", market="TW")
    if _full(tier):
        assert data["edge_count"] == 13
        assert (data["locked"], data["hidden_count"], data["sources_locked"]) == (False, 0, False)
        assert any(e["sources"] for e in data["edges"])
        return
    edges = data["edges"]
    customers = [e for e in edges if e["from_symbol"] == "9001" and e["edge_type"] == "supplies"]
    suppliers = [e for e in edges if e["to_symbol"] == "9001" and e["edge_type"] == "supplies"]
    competitors = [e for e in edges if e["edge_type"] == "competes"]
    assert [e["to_symbol"] for e in customers] == ["9014", "9013", "9012"]  # revenue desc
    assert len(suppliers) == 3
    assert len(competitors) == 3
    assert all(e["sources"] == [] for e in edges)
    assert data["sources_locked"] is True
    assert data["locked"] is True
    assert data["hidden_count"] == 13 - 9
    assert data["edge_count"] == 9
    assert "Pro plan" in data["upgrade_note"]


@pytest.mark.parametrize("tier", TIERS)
async def test_chain_position_by_tier(session_factory: async_sessionmaker, tier) -> None:
    data = await _call(session_factory, "get_chain_position", tier, id="9001", market="TW")
    if _full(tier):
        assert len(data["positions"]) == 2
        assert (data["locked"], data["hidden_count"]) == (False, 0)
        assert data["positions"][0]["reasons"]
        return
    assert [p["chain_key"] for p in data["positions"]] == ["chain-a"]
    assert data["positions"][0]["reasons"] == []
    assert (data["locked"], data["hidden_count"]) == (True, 1)
    assert "Pro plan" in data["upgrade_note"]


@pytest.mark.parametrize("tier", TIERS)
async def test_entity_graph_by_tier(session_factory: async_sessionmaker, tier) -> None:
    data = await _call(session_factory, "get_entity_graph", tier, id="9001", market="TW")
    kinds = {link["kind"] for link in data["links"]}
    node_ids = [n["id"] for n in data["nodes"]]
    if _full(tier):
        assert data["locked"] is False
        assert any(i.startswith("co:TW:") and i != "co:TW:9001" for i in node_ids)
        assert "cooccurrence" in kinds
        return
    assert data["locked"] is True
    assert data["hidden_count"] > 0
    assert [n["id"] for n in data["nodes"] if n["focus"]] == ["co:TW:9001"]
    assert len(node_ids) == 1 + PREVIEW_ENTITY_NODES
    assert all(i == "co:TW:9001" or i.startswith("ent:") for i in node_ids)
    assert kinds == {"mention"}


@pytest.mark.parametrize("tier", TIERS)
async def test_theme_list_lists_concepts_for_everyone(
    session_factory: async_sessionmaker, tier
) -> None:
    data = await _call(session_factory, "get_themes", tier)
    by_key = {t["key"]: t for t in data["themes"]}
    assert set(by_key) == {"wave", "other", "industry-x"}
    assert by_key["wave"]["description"] == "An invented theme"
    assert by_key["wave"]["member_count"] == 9
    assert data["concept_locked"] is (not _full(tier))


@pytest.mark.parametrize("tier", TIERS)
async def test_concept_theme_members_by_tier(session_factory: async_sessionmaker, tier) -> None:
    data = await _call(session_factory, "get_theme_members", tier, theme_key="wave")
    assert data["related_keys"] == ["other"]
    assert data["description"] == "An invented theme"
    assert data["member_count"] == 9
    if _full(tier):
        assert len(data["members"]) == 9
        assert (data["locked"], data["hidden_count"]) == (False, 0)
        return
    by_position: dict[str, list[str]] = {}
    for m in data["members"]:
        by_position.setdefault(m["position"], []).append(m["symbol"])
    assert {k: len(v) for k, v in by_position.items()} == {
        "upstream": 3,
        "related": 3,
        "midstream": 1,
    }
    assert (data["locked"], data["hidden_count"]) == (True, 9 - 7)
    assert "Pro plan" in data["upgrade_note"]


@pytest.mark.parametrize("tier", TIERS)
async def test_industry_theme_members_are_never_limited(
    session_factory: async_sessionmaker, tier
) -> None:
    data = await _call(session_factory, "get_theme_members", tier, theme_key="industry-x")
    assert len(data["members"]) == 6
    assert (data["locked"], data["hidden_count"]) == (False, 0)


@pytest.mark.parametrize("tier", TIERS)
async def test_symbol_themes_list_concept_names_for_everyone(
    session_factory: async_sessionmaker, tier
) -> None:
    data = await _call(session_factory, "get_symbol_themes", tier, id="9001", market="TW")
    concept = [t for t in data["themes"] if t["kind"] == "concept"]
    assert [t["theme_key"] for t in concept] == ["wave"]
    assert concept[0]["name_zh"] == "浪潮"
    if _full(tier):
        assert concept[0]["sub_industry"] != ""  # the symbol's position in the theme
        assert data["concept_locked"] is False
    else:
        assert concept[0]["sub_industry"] == ""
        assert data["concept_locked"] is True


async def test_each_tier_is_resolved_per_call_not_cached(
    session_factory: async_sessionmaker,
) -> None:
    """The same tool instance serves a Free then a Pro caller: nothing is remembered between."""
    registry = ToolRegistry()
    for tool, immediate in build_registration_table(session_factory):
        registry.register(tool, immediate=immediate)
    tool = registry.get_tool("get_supply_chain_edges")
    assert tool is not None
    args = tool.input_model.model_validate({"id": "9001", "market": "TW"})
    free = await dispatch_tool(tool, args, _ctx(Tier.FREE))
    pro = await dispatch_tool(tool, args, _ctx(Tier.PRO))
    free_again = await dispatch_tool(tool, args, _ctx(Tier.FREE))
    assert free.model_dump() == free_again.model_dump()
    assert pro.model_dump()["edge_count"] > free.model_dump()["edge_count"]
