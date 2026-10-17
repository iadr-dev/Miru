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
from datetime import date as date_

import anyio.to_thread
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.supply_chain.edges import ScEdge, to_edges
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
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput

_DEFAULT_MEMBER_LIMIT = 200


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


class GetThemesOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_count: int = 0
    themes: tuple[ThemeSummary, ...] = Field(default_factory=tuple)


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
        del ctx
        typed = parse_args(GetThemesInput, args)
        status = typed.status if typed.status is not None else ThemeStatus.ACTIVE
        async with self._session_factory() as session:
            store: ThemeStore = SqlAlchemyThemeStore(session)
            themes = await store.list_themes(kind=typed.kind, status=status)
            summaries = [
                ThemeSummary(
                    key=theme.key,
                    name_zh=theme.name_zh,
                    name_en=theme.name_en,
                    kind=theme.kind,
                    status=theme.status,
                    member_count=await store.count_theme_members(theme_key=theme.key),
                )
                for theme in themes
            ]
        return GetThemesOutput(theme_count=len(summaries), themes=tuple(summaries))


class GetThemeMembersInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_key: str = Field(min_length=1, description="Theme slug/key, e.g. '半導體'.")
    limit: int = Field(
        default=_DEFAULT_MEMBER_LIMIT,
        ge=1,
        le=1000,
        description="Maximum member rows to return (members are ordered by symbol).",
    )


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
    members: tuple[ThemeMember, ...] = Field(default_factory=tuple)
    message: str = ""


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
        del ctx
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
        return GetThemeMembersOutput(
            theme_key=theme.key,
            found=True,
            name_zh=theme.name_zh,
            name_en=theme.name_en,
            kind=theme.kind,
            member_count=len(members),
            members=tuple(members[: typed.limit]),
        )


class GetSymbolThemesOutput(BaseModel):
    """The reverse lookup — every theme a symbol belongs to, with that membership's own
    sub-industry and confidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    theme_count: int = 0
    themes: tuple[SymbolTheme, ...] = Field(default_factory=tuple)


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
        del ctx
        typed = parse_args(SymbolInput, args)
        async with self._session_factory() as session:
            store: ThemeStore = SqlAlchemyThemeStore(session)
            themes = await store.get_symbol_themes(symbol=typed.id, market=typed.market.value)
        return GetSymbolThemesOutput(
            symbol=typed.id,
            market=typed.market.value,
            theme_count=len(themes),
            themes=tuple(themes),
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
        del ctx
        typed = parse_args(SymbolInput, args)
        if typed.market not in (Market.TW, Market.US):
            return GetSupplyChainEdgesOutput(symbol=typed.id, market=typed.market.value)
        async with self._session_factory() as session:
            relations = await SqlAlchemySupplyChainStore(session).get_relations_for_symbol(
                typed.id, "TW" if typed.market is Market.TW else "US"
            )
        edges = to_edges(relations)
        return GetSupplyChainEdgesOutput(
            symbol=typed.id, market=typed.market.value, edge_count=len(edges), edges=edges
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
    """This module's `(tool, immediate)` registration table. All five are deferred long-tail
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
        (GetThemeTierHeatmapTool(session_factory, store), False),
    ]
    return tools


__all__ = [
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
    "ThemeSummary",
    "build_registration_table",
]
