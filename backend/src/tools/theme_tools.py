"""Theme/tier lookup tools over the relational theme store.

These are the agent's read surface onto the ingested industry-chain + concept-research theme
layer: what's in a theme, which themes a symbol belongs to, and a theme's upstream/midstream/
downstream tier breakdown. Unlike the market tools (which name a capability and fetch through the
router), these read already-stored relational data, so each takes the app's injected session
factory and opens one short read session per call — the caller never fetches a source live.

Classification/relationship metadata carries no scoring and no buy/sell framing, so it is `public`
at the tool level — the same tier at which `get_company_profile` returns its sector/industry
classification and `get_institutional_flow` returns its whole-market-ingested rows. The underlying
industry-chain source is licensed, but once ingested and stored relationally the derived theme
data follows the established convention that a derived-store read is classed by what it exposes,
not by its origin adapter's tier.

Concept-theme membership is AI-researched and carries a per-member `confidence`; industry-chain
membership is editorially sourced at `confidence` 1.0. Every member row surfaces `source` and
`confidence` so a caller can tell the two apart and never present a researched guess as an
editorially-guaranteed fact.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar, calendar_from_known_dates
from collections.abc import Sequence
from datetime import date as date_
from datetime import datetime

import anyio.to_thread
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.supply_chain.domain.concepts import ConceptPosition
from analytics.supply_chain.domain.models import Kind, MarketCode
from analytics.supply_chain.edges import ScEdge, to_edges
from analytics.supply_chain.entity_graph import (
    DEFAULT_LIMIT as ENTITY_GRAPH_DEFAULT_LIMIT,
)
from analytics.supply_chain.entity_graph import (
    MAX_ENTITIES,
    EntityGraph,
    build_entity_graph,
)
from analytics.supply_chain.positions.models import Method, Position, ReasonCode
from analytics.supply_chain.store.concept_store import SqlAlchemyConceptStore
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from analytics.symbol_universe import is_scoreable_symbol
from analytics.themes.models import (
    SymbolTheme,
    ThemeKind,
    ThemeMember,
    ThemeStatus,
    ThemeTier,
    ThemeTierName,
    ThemeTierSource,
)
from analytics.themes.store import SqlAlchemyThemeStore, ThemeStore
from config.settings import get_settings
from ingest.jobs import PRICE_EOD_JOB
from ingest.store import ParquetStore
from schemas.enums import DataClass, Market
from schemas.feature_tiers import (
    PREVIEW_CHAIN_ROWS,
    PREVIEW_CONCEPT_MEMBERS_PER_POSITION,
    PREVIEW_ENTITY_NODES,
    PREVIEW_PEERS_PER_ROLE,
    UPGRADE_NOTE,
    access_for,
)
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput

_DEFAULT_MEMBER_LIMIT = 200

# The `FEATURE_MIN_TIER` rows whose full view is Pro-only. Below Pro the tools below return the
# preview itself (server-side), so every route, the agent and MCP see the same limited data.
SUPPLY_CHAIN_FEATURE = "supply_chain"
CONCEPT_THEMES_FEATURE = "concept_themes"


class GetThemesInput(BaseModel):
    """No parameters required — `kind`/`status` narrow an otherwise whole-catalog listing.
    `status` defaults to `active` (never surface a `retired` theme to a caller browsing "what
    themes exist" unless explicitly asked for one) — `None` here means "use that default", not
    "no filter"; pass `status="proposed"`/`"retired"` explicitly to see those."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ThemeKind | None = None
    status: ThemeStatus | None = None


class ThemeSummary(BaseModel):
    """One theme's discovery-card shape — identity plus its member count, so a caller (the
    screener's `PresetLibrary`-style theme picker) can show "半導體 (128)" without a second
    round-trip per theme."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name_zh: str
    name_en: str = ""
    kind: ThemeKind
    status: ThemeStatus
    member_count: int = 0
    description: str = ""
    related_keys: tuple[str, ...] = ()


class GetThemesOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_count: int = 0
    themes: tuple[ThemeSummary, ...] = Field(default_factory=tuple)
    # True for a caller below Pro: concept themes are listed, but their member detail is limited.
    concept_locked: bool = False


class GetThemesTool(BaseTool):
    """`get_themes` — the discovery-level list of every theme in the catalog (industry-chain +
    concept), each with its member count. The screener's 主題 Themes sub-tab reads this to render
    theme chips; selecting one loads that theme's members as a pre-filtered screen via the
    existing `get_theme_members` lookup — a theme is a tag, never a scored factor."""

    name = "get_themes"
    description = (
        "List every theme in the catalog (industry-chain and concept), each with its member "
        "count. Defaults to active themes only."
    )
    input_model: type[BaseModel] = GetThemesInput
    output_model: type[BaseModel] = GetThemesOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "themes"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetThemesInput, args)
        status = typed.status if typed.status is not None else ThemeStatus.ACTIVE
        async with self._session_factory() as session:
            store: ThemeStore = SqlAlchemyThemeStore(session)
            themes = await store.list_themes(kind=typed.kind, status=status)
            concept = await SqlAlchemyConceptStore(session).concept_info()
            summaries = [
                ThemeSummary(
                    key=theme.key,
                    name_zh=theme.name_zh,
                    name_en=theme.name_en,
                    kind=theme.kind,
                    status=theme.status,
                    member_count=await store.count_theme_members(theme_key=theme.key),
                    description=concept[theme.key].description if theme.key in concept else "",
                    related_keys=concept[theme.key].related_keys if theme.key in concept else (),
                )
                for theme in themes
            ]
        return GetThemesOutput(
            theme_count=len(summaries),
            themes=tuple(summaries),
            concept_locked=not access_for(CONCEPT_THEMES_FEATURE, ctx.tier).full,
        )


class GetThemeMembersInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_key: str = Field(min_length=1, description="Theme slug/key, e.g. '半導體'.")
    limit: int = Field(
        default=_DEFAULT_MEMBER_LIMIT,
        ge=1,
        le=1000,
        description="Maximum member rows to return (members are ordered by symbol).",
    )


class ThemeMemberDetail(ThemeMember):
    """A membership row; `position` is set for a concept theme's members (upstream / midstream /
    downstream / related) and null for an industry theme, whose tiers sit on sub-industries."""

    position: ConceptPosition | None = None


class GetThemeMembersOutput(BaseModel):
    """A theme's identity plus its member symbols, each carrying the per-membership
    `sub_industry`/`confidence`/`source` so an AI-researched concept member is never presented as
    an editorially-guaranteed industry-chain one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_key: str
    found: bool
    name_zh: str = ""
    name_en: str = ""
    kind: ThemeKind | None = None
    member_count: int = 0
    members: tuple[ThemeMemberDetail, ...] = Field(default_factory=tuple)
    message: str = ""
    description: str = ""
    related_keys: tuple[str, ...] = ()
    # Preview of a concept theme for a caller below Pro: `locked` when members were withheld,
    # `hidden_count` how many (`member_count` stays the true total).
    locked: bool = False
    hidden_count: int = 0
    upgrade_note: str = ""


def preview_concept_members(
    members: Sequence[ThemeMemberDetail],
) -> tuple[tuple[ThemeMemberDetail, ...], int]:
    """Pure: the first `PREVIEW_CONCEPT_MEMBERS_PER_POSITION` members of each position
    (upstream / midstream / downstream / related), in the theme's own member order, and how many
    were withheld."""
    taken: dict[str, int] = {}
    kept: list[ThemeMemberDetail] = []
    for member in members:
        group = member.position.value if member.position is not None else ""
        if taken.get(group, 0) < PREVIEW_CONCEPT_MEMBERS_PER_POSITION:
            taken[group] = taken.get(group, 0) + 1
            kept.append(member)
    return tuple(kept), len(members) - len(kept)


class GetThemeMembersTool(BaseTool):
    """`get_theme_members` — the symbols in a theme, with each membership's sub-industry,
    confidence, and provenance. An unknown key returns `found=False` with a clear message, never
    an error."""

    name = "get_theme_members"
    description = (
        "Get the member symbols of a theme (industry-chain or concept), each with its "
        "sub-industry, confidence score, and source (industry-chain vs AI-researched)."
    )
    input_model: type[BaseModel] = GetThemeMembersInput
    output_model: type[BaseModel] = GetThemeMembersOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "theme_members"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetThemeMembersInput, args)
        async with self._session_factory() as session:
            store: ThemeStore = SqlAlchemyThemeStore(session)
            theme = await store.get_theme_by_key(key=typed.theme_key)
            if theme is None:
                return GetThemeMembersOutput(
                    theme_key=typed.theme_key,
                    found=False,
                    message=f"No theme found for key {typed.theme_key!r}.",
                )
            members = await store.get_theme_member_details(theme_key=typed.theme_key)
            concepts = SqlAlchemyConceptStore(session)
            info = (await concepts.concept_info([theme.key])).get(theme.key)
            positions = await concepts.concept_positions(theme.key) if info else {}
        detailed = [
            ThemeMemberDetail(
                **member.model_dump(), position=positions.get((member.symbol, member.market))
            )
            for member in members
        ]
        hidden = 0
        if (
            theme.kind is ThemeKind.CONCEPT
            and not access_for(CONCEPT_THEMES_FEATURE, ctx.tier).full
        ):
            kept, hidden = preview_concept_members(detailed)
            detailed = list(kept)
        return GetThemeMembersOutput(
            theme_key=theme.key,
            found=True,
            name_zh=theme.name_zh,
            name_en=theme.name_en,
            kind=theme.kind,
            member_count=len(members),
            members=tuple(detailed[: typed.limit]),
            description=info.description if info else "",
            related_keys=info.related_keys if info else (),
            locked=hidden > 0,
            hidden_count=hidden,
            upgrade_note=UPGRADE_NOTE if hidden > 0 else "",
        )


class GetSymbolThemesOutput(BaseModel):
    """The reverse lookup — every theme a symbol belongs to, with that membership's own
    sub-industry and confidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    theme_count: int = 0
    themes: tuple[SymbolTheme, ...] = Field(default_factory=tuple)
    # True when, for a caller below Pro, the per-membership position of the concept themes listed
    # was withheld (the concept themes themselves, by name, are always listed).
    concept_locked: bool = False


class GetSymbolThemesTool(BaseTool):
    """`get_symbol_themes` — the themes one symbol belongs to (the reverse of
    `get_theme_members`). Answers "what themes is 2330 in". An unknown symbol returns an empty
    theme list, never an error."""

    name = "get_symbol_themes"
    description = (
        "Get every theme a stock/ETF symbol belongs to (industry-chain and concept), with each "
        "membership's sub-industry and confidence — the reverse of get_theme_members."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetSymbolThemesOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "symbol_themes"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        async with self._session_factory() as session:
            store: ThemeStore = SqlAlchemyThemeStore(session)
            themes = await store.get_symbol_themes(symbol=typed.id, market=typed.market.value)
        concept_locked = False
        if not access_for(CONCEPT_THEMES_FEATURE, ctx.tier).full:
            # Names only: a concept membership's `sub_industry` is the symbol's position in it.
            concept_locked = any(t.kind is ThemeKind.CONCEPT and t.sub_industry for t in themes)
            themes = [
                t.model_copy(update={"sub_industry": ""}) if t.kind is ThemeKind.CONCEPT else t
                for t in themes
            ]
        return GetSymbolThemesOutput(
            symbol=typed.id,
            market=typed.market.value,
            theme_count=len(themes),
            themes=tuple(themes),
            concept_locked=concept_locked,
        )


class GetThemeTiersInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_key: str = Field(min_length=1, description="Theme slug/key, e.g. '半導體'.")


class GetThemeTiersOutput(BaseModel):
    """A theme's sub-industry tier breakdown, grouped into upstream/midstream/downstream. Each
    tier assignment carries its `source` (curated editorial map vs keyword heuristic) so a caller
    can tell an editorially-verified position from an automatically-classified one and never
    present a heuristic guess as equally authoritative."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_key: str
    found: bool
    upstream: tuple[ThemeTier, ...] = Field(default_factory=tuple)
    midstream: tuple[ThemeTier, ...] = Field(default_factory=tuple)
    downstream: tuple[ThemeTier, ...] = Field(default_factory=tuple)
    message: str = ""


class GetThemeTiersTool(BaseTool):
    """`get_theme_tiers` — a theme's sub-industries grouped by upstream/midstream/downstream
    position, each assignment disclosing whether an editor (curated) or a keyword heuristic
    assigned it. An unknown key returns `found=False`, never an error."""

    name = "get_theme_tiers"
    description = (
        "Get a theme's sub-industries grouped into upstream/midstream/downstream tiers, each "
        "assignment marked curated (editorial) or heuristic (automatically classified)."
    )
    input_model: type[BaseModel] = GetThemeTiersInput
    output_model: type[BaseModel] = GetThemeTiersOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "theme_tiers"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(GetThemeTiersInput, args)
        async with self._session_factory() as session:
            store: ThemeStore = SqlAlchemyThemeStore(session)
            theme = await store.get_theme_by_key(key=typed.theme_key)
            if theme is None:
                return GetThemeTiersOutput(
                    theme_key=typed.theme_key,
                    found=False,
                    message=f"No theme found for key {typed.theme_key!r}.",
                )
            tiers = await store.get_theme_tiers(theme_key=typed.theme_key)
        by_tier: dict[ThemeTierName, list[ThemeTier]] = {name: [] for name in ThemeTierName}
        for tier in tiers:
            by_tier[tier.tier].append(tier)
        return GetThemeTiersOutput(
            theme_key=theme.key,
            found=True,
            upstream=tuple(by_tier[ThemeTierName.UPSTREAM]),
            midstream=tuple(by_tier[ThemeTierName.MIDSTREAM]),
            downstream=tuple(by_tier[ThemeTierName.DOWNSTREAM]),
        )


class GetSupplyChainEdgesOutput(BaseModel):
    """Every published supply-chain edge where the company is either endpoint — the 供應鏈關係圖
    card's one data source. `edges` carries both directions; each edge's `from_symbol`/`to_symbol`
    (with `from_market`/`to_market`) disambiguates. Only verified, non-stale relations appear
    (candidates, rejected and stale rows are hidden). Each edge states its `grade`
    (`confidence` is the legacy name for the same value), product, as-of date and source links.
    Empty, not an error, for a company with no published relations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    edge_count: int = 0
    edges: tuple[ScEdge, ...] = Field(default_factory=tuple)
    # Preview for a caller below Pro: at most a few peers per role, no source links. `locked` when
    # anything was withheld, `hidden_count` how many edges, `sources_locked` that links are Pro.
    locked: bool = False
    hidden_count: int = 0
    sources_locked: bool = False
    upgrade_note: str = ""


_GRADE_ORDER = {"high": 0, "medium": 1, "low": 2}
_ROLE_ORDER = ("supplier", "customer", "competitor")


def _edge_role(edge: ScEdge, symbol: str, market: str) -> str:
    """The peer's role relative to the focus company: a supplier sells to it, a customer buys
    from it, a competitor competes with it."""
    if edge.edge_type is Kind.COMPETES:
        return "competitor"
    focus_supplies = edge.from_symbol == symbol and edge.from_market == market
    return "customer" if focus_supplies else "supplier"


def _edge_peer(edge: ScEdge, symbol: str, market: str) -> tuple[str, str, str]:
    if edge.from_symbol == symbol and edge.from_market == market:
        return edge.to_symbol, edge.to_market, edge.to_name
    return edge.from_symbol, edge.from_market, edge.from_name


def preview_supply_edges(
    edges: Sequence[ScEdge], symbol: str, market: str
) -> tuple[tuple[ScEdge, ...], int, bool]:
    """Pure: the preview of a company's supply-chain edges - per role, the best edge of each of
    the top `PREVIEW_PEERS_PER_ROLE` peers (grade high>medium>low, then revenue share descending
    with unknown last, then name), each with its source links removed. Returns the edges kept, how
    many edges were withheld, and whether any source link was removed."""
    by_role: dict[str, dict[tuple[str, str], tuple[tuple[object, ...], ScEdge]]] = {}
    for edge in edges:
        role = _edge_role(edge, symbol, market)
        peer_symbol, peer_market, peer_name = _edge_peer(edge, symbol, market)
        revenue = edge.revenue_pct
        key: tuple[object, ...] = (
            _GRADE_ORDER[edge.grade.value],
            revenue is None,
            -(revenue or 0.0),
            peer_name or peer_symbol,
            peer_symbol,
        )
        best = by_role.setdefault(role, {})
        peer = (peer_symbol, peer_market)
        if peer not in best or key < best[peer][0]:
            best[peer] = (key, edge)
    kept: list[ScEdge] = []
    stripped = False
    for role in _ROLE_ORDER:
        ranked = sorted(by_role.get(role, {}).values(), key=lambda item: item[0])
        for _, edge in ranked[:PREVIEW_PEERS_PER_ROLE]:
            stripped = stripped or bool(edge.sources)
            kept.append(edge.model_copy(update={"sources": ()}))
    return tuple(kept), len(edges) - len(kept), stripped


class GetSupplyChainEdgesTool(BaseTool):
    """`get_supply_chain_edges` — every published supplier/customer/competitor relationship
    involving one company (matched on symbol AND market), from either side. Relationship
    metadata, same `public` tier as `get_symbol_themes`."""

    name = "get_supply_chain_edges"
    description = (
        "Get every published supply-chain edge (supplies/competes) involving one company, as "
        "either the from- or to-side, each with its evidence grade, product, as-of date and "
        "source links. Candidate and stale relations are not returned."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetSupplyChainEdgesOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "supply_chain_edges"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        if typed.market not in (Market.TW, Market.US):
            return GetSupplyChainEdgesOutput(symbol=typed.id, market=typed.market.value)
        async with self._session_factory() as session:
            relations = await SqlAlchemySupplyChainStore(session).get_relations_for_symbol(
                typed.id, "TW" if typed.market is Market.TW else "US"
            )
        edges = to_edges(relations)
        if access_for(SUPPLY_CHAIN_FEATURE, ctx.tier).full:
            return GetSupplyChainEdgesOutput(
                symbol=typed.id, market=typed.market.value, edge_count=len(edges), edges=edges
            )
        kept, hidden, stripped = preview_supply_edges(edges, typed.id, typed.market.value)
        return GetSupplyChainEdgesOutput(
            symbol=typed.id,
            market=typed.market.value,
            edge_count=len(kept),
            edges=kept,
            locked=hidden > 0 or stripped,
            hidden_count=hidden,
            sources_locked=True,
            upgrade_note=UPGRADE_NOTE if hidden > 0 or stripped else "",
        )


class ChainReason(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ReasonCode
    detail: str = ""


class ChainPositionItem(BaseModel):
    """One industry chain the company sits in. `method` says how the position was reached:
    `override` (an operator decision), `evidence` (verified supply relations, alone or
    confirming the tier map) or `prior` (the sub-industry tier map only; low confidence when the
    reason is `prior_keyword`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chain_key: str
    chain_name: str
    position: Position
    is_primary: bool
    method: Method
    confidence: float
    reasons: tuple[ChainReason, ...] = ()
    computed_at: datetime | None = None


class GetChainPositionOutput(BaseModel):
    """Where a company sits (upstream/midstream/downstream) in each industry chain it belongs to.
    Empty `positions` means unclassified (尚未分類) - the classifier abstains rather than guesses.
    A descriptive classification, never a rating."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    positions: tuple[ChainPositionItem, ...] = Field(default_factory=tuple)
    # Preview for a caller below Pro: the primary chain only, without its reason codes.
    locked: bool = False
    hidden_count: int = 0
    upgrade_note: str = ""


def preview_positions(
    items: Sequence[ChainPositionItem],
) -> tuple[tuple[ChainPositionItem, ...], int, bool]:
    """Pure: only the primary chain row (else the first), `reasons` removed. Returns the rows
    kept, how many chains were withheld, and whether reasons were removed."""
    if not items:
        return (), 0, False
    primary = next((i for i in items if i.is_primary), items[0])
    kept = (primary.model_copy(update={"reasons": ()}),)[:PREVIEW_CHAIN_ROWS]
    return kept, len(items) - len(kept), bool(primary.reasons)


class GetChainPositionTool(BaseTool):
    """`get_chain_position` - a company's computed position in each of its industry chains."""

    name = "get_chain_position"
    description = (
        "Get where one company sits (upstream/midstream/downstream) in each industry chain it "
        "belongs to, with the method, confidence and reason codes behind each position. An empty "
        "list means not yet classified."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetChainPositionOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = None

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        if typed.market not in (Market.TW, Market.US):
            return GetChainPositionOutput(symbol=typed.id, market=typed.market.value)
        async with self._session_factory() as session:
            rows = await SqlAlchemySupplyChainStore(session).get_positions_for_symbol(
                typed.id, "TW" if typed.market is Market.TW else "US"
            )
        items = tuple(
            ChainPositionItem(
                chain_key=r.chain_key,
                chain_name=r.chain_name,
                position=r.position,
                is_primary=r.is_primary,
                method=r.method,
                confidence=r.confidence,
                reasons=tuple(ChainReason(code=x.code, detail=x.detail) for x in r.reasons),
                computed_at=r.computed_at,
            )
            for r in rows
        )
        if access_for(SUPPLY_CHAIN_FEATURE, ctx.tier).full:
            return GetChainPositionOutput(
                symbol=typed.id, market=typed.market.value, positions=items
            )
        kept, hidden, stripped = preview_positions(items)
        return GetChainPositionOutput(
            symbol=typed.id,
            market=typed.market.value,
            positions=kept,
            locked=hidden > 0 or stripped,
            hidden_count=hidden,
            upgrade_note=UPGRADE_NOTE if hidden > 0 or stripped else "",
        )


class GetEntityGraphInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, description="Exchange-scoped symbol id, e.g. '2330'.")
    market: MarketCode = "TW"
    min_weight: int = Field(
        default=1, ge=1, description="Drop entities the company mentions fewer times than this."
    )
    limit: int = Field(
        default=ENTITY_GRAPH_DEFAULT_LIMIT,
        ge=1,
        le=MAX_ENTITIES,
        description="Maximum entity nodes (the most-mentioned first).",
    )


class GetEntityGraphOutput(EntityGraph):
    """The entity graph, plus the preview markers: for a caller below Pro only the focus company
    and its top entities (no peer companies, no peer or supply links); `hidden_count` nodes
    withheld."""

    locked: bool = False
    hidden_count: int = 0
    upgrade_note: str = ""


def preview_entity_graph(graph: EntityGraph) -> tuple[EntityGraph, int]:
    """Pure: the focus node plus the top `PREVIEW_ENTITY_NODES` entity nodes by weight (peer
    company nodes dropped), with only the focus's own mention links to the kept entities. Returns
    the reduced graph and how many nodes were withheld."""
    focus = [n for n in graph.nodes if n.focus]
    focus_ids = {n.id for n in focus}
    entities = sorted(
        (n for n in graph.nodes if not n.focus and n.id.startswith("ent:")),
        key=lambda n: (-n.weight, n.id),
    )[:PREVIEW_ENTITY_NODES]
    kept_ids = focus_ids | {n.id for n in entities}
    links = tuple(
        link
        for link in graph.links
        if link.kind == "mention" and link.source in focus_ids and link.target in kept_ids
    )
    nodes = (*focus, *entities)
    reduced = EntityGraph(symbol=graph.symbol, market=graph.market, nodes=nodes, links=links)
    return reduced, len(graph.nodes) - len(nodes)


class GetEntityGraphTool(BaseTool):
    """`get_entity_graph` - the 關聯圖譜 ego graph of one company: the entities (technologies,
    materials, applications, products, other companies) its reports mention, the companies that
    mention the same ones, and the verified supply relations among the companies shown. Only ids,
    labels, types and counts; descriptive, never a rating."""

    name = "get_entity_graph"
    description = (
        "Get a company's entity graph: the entities it mentions (weighted by mention count), the "
        "peer companies sharing them (weighted by shared entities), and verified supply links "
        "(supplier to customer) among those shown. Only the company when nothing is indexed."
    )
    input_model: type[BaseModel] = GetEntityGraphInput
    output_model: type[BaseModel] = GetEntityGraphOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = None

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetEntityGraphInput, args)
        async with self._session_factory() as session:
            graph = await build_entity_graph(
                SqlAlchemyConceptStore(session),
                SqlAlchemySupplyChainStore(session),
                typed.id,
                typed.market,
                min_weight=typed.min_weight,
                limit=typed.limit,
            )
        if access_for(SUPPLY_CHAIN_FEATURE, ctx.tier).full:
            return GetEntityGraphOutput(**graph.model_dump())
        reduced, hidden = preview_entity_graph(graph)
        return GetEntityGraphOutput(
            **reduced.model_dump(),
            locked=hidden > 0,
            hidden_count=hidden,
            upgrade_note=UPGRADE_NOTE if hidden > 0 else "",
        )


def _real_calendar(store: ParquetStore, market: str) -> TradingDayCalendar:
    """The real trading-day calendar for `market` — duplicated from `tools/market/`'s
    own private helper of the same name/shape rather than imported, matching this codebase's
    existing convention that each tool module builds its own store-reading helpers instead of
    reaching into another module's private functions."""
    known_dates = store.list_partition_dates(PRICE_EOD_JOB.dataset, market)
    if not known_dates:
        return TradingDayCalendar()
    return calendar_from_known_dates(known_dates)


def _closes_by_symbol(store: ParquetStore, market: str, trade_date: date_) -> dict[str, float]:
    """Every real equity/ETF symbol's closing price on `trade_date` — same read + warrant-
    filtering `tools/market/_common.py::closes_by_symbol` performs, duplicated here for the same
    reason `_real_calendar` above is."""
    raw_bars = store.read_partition(PRICE_EOD_JOB.dataset, market, trade_date)
    closes: dict[str, float] = {}
    for row in raw_bars:
        symbol = row.get("symbol")
        close = row.get("close")
        if (
            isinstance(symbol, str)
            and is_scoreable_symbol(symbol)
            and isinstance(close, (int, float))
        ):
            closes[symbol] = float(close)
    return closes


class SubIndustryHeat(BaseModel):
    """One sub-industry tile — `pct_change` is the simple average close-to-close percentage
    change across every one of that sub-industry's member symbols that had a real close on both
    days (never a partial figure silently presented as the whole group's), `null` when none did.
    `member_count`/`priced_member_count` let a caller show "產業鏈一製藥" or the reference app's
    own member-count style label; a large gap between the two means most members had no price
    data for this window, which the frontend should treat as a thinner signal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sub_industry: str
    tier: ThemeTierName
    tier_source: ThemeTierSource
    pct_change: float | None = None
    member_count: int = 0
    priced_member_count: int = 0


class GetThemeTierHeatmapOutput(BaseModel):
    """A theme's upstream/midstream/downstream sub-industry tiles, each colored by its own
    members' real average price change — the 產業鏈 tier-tile view's one data source. `found` is
    `False` only when the theme key itself doesn't exist; a found theme with no priced members
    yet still returns its tiles, each with `pct_change=null`, never a fabricated 0%."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_key: str
    found: bool
    trade_date: date_ | None = None
    prior_trade_date: date_ | None = None
    data_available: bool = True
    unavailable_reason: str | None = None
    upstream: tuple[SubIndustryHeat, ...] = Field(default_factory=tuple)
    midstream: tuple[SubIndustryHeat, ...] = Field(default_factory=tuple)
    downstream: tuple[SubIndustryHeat, ...] = Field(default_factory=tuple)
    message: str = ""


class GetThemeTierHeatmapTool(BaseTool):
    """`get_theme_tier_heatmap` — a theme's tier tiles (see `get_theme_tiers`), each additionally
    colored by the real average close-to-close price change of that sub-industry's own member
    symbols over the latest available trading day. Reads both the relational theme store (tier +
    membership) and the ingest-owned Parquet price store (closes) — the one tool in this module
    that needs both seams."""

    name = "get_theme_tier_heatmap"
    description = (
        "Get a theme's upstream/midstream/downstream sub-industry tiles, each colored by that "
        "sub-industry's own members' real average price change over the latest trading day."
    )
    input_model: type[BaseModel] = GetThemeTiersInput
    output_model: type[BaseModel] = GetThemeTierHeatmapOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "theme_tier_heatmap"

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], store: ParquetStore
    ) -> None:
        self._session_factory = session_factory
        self._store = store

    def _read_prices(
        self, market: str
    ) -> tuple[date_, date_, dict[str, float], dict[str, float]] | None:
        """Every blocking Parquet read the heatmap needs, in one pass; `None` = nothing ingested."""
        calendar = _real_calendar(self._store, market)
        trade_date = _latest_price_date(self._store, market)
        if trade_date is None:
            return None
        prior_date = calendar.shift_business_days(trade_date, -1)
        return (
            trade_date,
            prior_date,
            _closes_by_symbol(self._store, market, trade_date),
            _closes_by_symbol(self._store, market, prior_date),
        )

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(GetThemeTiersInput, args)
        market = Market.TW.value
        async with self._session_factory() as session:
            store: ThemeStore = SqlAlchemyThemeStore(session)
            theme = await store.get_theme_by_key(key=typed.theme_key)
            if theme is None:
                return GetThemeTierHeatmapOutput(
                    theme_key=typed.theme_key,
                    found=False,
                    message=f"No theme found for key {typed.theme_key!r}.",
                )
            tiers = await store.get_theme_tiers(theme_key=typed.theme_key)
            members = await store.get_theme_member_details(theme_key=typed.theme_key)

        prices = await anyio.to_thread.run_sync(self._read_prices, market)
        if prices is None:
            return GetThemeTierHeatmapOutput(
                theme_key=theme.key,
                found=True,
                data_available=False,
                unavailable_reason="No price data ingested yet for this market.",
            )
        trade_date, prior_date, today_closes, prior_closes = prices

        symbols_by_sub_industry: dict[str, list[str]] = {}
        for member in members:
            symbols_by_sub_industry.setdefault(member.sub_industry, []).append(member.symbol)

        by_tier: dict[ThemeTierName, list[SubIndustryHeat]] = {name: [] for name in ThemeTierName}
        for tier_row in tiers:
            symbols = symbols_by_sub_industry.get(tier_row.sub_industry, [])
            changes = [
                (today_closes[s] / prior_closes[s] - 1) * 100
                for s in symbols
                if s in today_closes and s in prior_closes and prior_closes[s] != 0
            ]
            by_tier[tier_row.tier].append(
                SubIndustryHeat(
                    sub_industry=tier_row.sub_industry,
                    tier=tier_row.tier,
                    tier_source=tier_row.source,
                    pct_change=sum(changes) / len(changes) if changes else None,
                    member_count=len(symbols),
                    priced_member_count=len(changes),
                )
            )
        return GetThemeTierHeatmapOutput(
            theme_key=theme.key,
            found=True,
            trade_date=trade_date,
            prior_trade_date=prior_date,
            upstream=tuple(by_tier[ThemeTierName.UPSTREAM]),
            midstream=tuple(by_tier[ThemeTierName.MIDSTREAM]),
            downstream=tuple(by_tier[ThemeTierName.DOWNSTREAM]),
        )


def _latest_price_date(store: ParquetStore, market: str) -> date_ | None:
    """The most recent trading day this market's `PRICE_EOD_JOB` partition tree actually has —
    same lookup `tools/market/_common.py::latest_dataset_date` performs, duplicated here for the
    same cross-module-private-import reason `_real_calendar` above documents."""
    known_dates = store.list_partition_dates(PRICE_EOD_JOB.dataset, market)
    return max(known_dates) if known_dates else None


def build_registration_table(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table. All are deferred long-tail
    reads. Unlike the router-backed tool groups, these read the relational theme store, so this
    table takes the app's session factory rather than the data-access router;
    `get_theme_tier_heatmap` additionally builds its own `ParquetStore` (same
    `tools/market/__init__.py::build_registration_table` pattern) since it also needs real
    prices."""
    store = ParquetStore(root=get_settings().parquet_root)
    tools: list[tuple[Tool, bool]] = [
        (GetThemesTool(session_factory), False),
        (GetThemeMembersTool(session_factory), False),
        (GetSymbolThemesTool(session_factory), False),
        (GetThemeTiersTool(session_factory), False),
        (GetSupplyChainEdgesTool(session_factory), False),
        (GetChainPositionTool(session_factory), False),
        (GetEntityGraphTool(session_factory), False),
        (GetThemeTierHeatmapTool(session_factory, store), False),
    ]
    return tools


__all__ = [
    "ChainPositionItem",
    "ChainReason",
    "GetChainPositionOutput",
    "GetChainPositionTool",
    "GetEntityGraphInput",
    "GetEntityGraphTool",
    "GetSupplyChainEdgesOutput",
    "GetSupplyChainEdgesTool",
    "GetSymbolThemesOutput",
    "GetSymbolThemesTool",
    "GetThemeMembersInput",
    "GetThemeMembersOutput",
    "GetThemeMembersTool",
    "GetThemeTierHeatmapOutput",
    "GetThemeTierHeatmapTool",
    "GetThemeTiersInput",
    "GetThemeTiersOutput",
    "GetThemeTiersTool",
    "GetThemesInput",
    "GetThemesOutput",
    "GetThemesTool",
    "SubIndustryHeat",
    "ThemeMemberDetail",
    "ThemeSummary",
    "build_registration_table",
]
