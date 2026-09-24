"""yfinance-native, non-TW-primary capability tools — options chain, institutional/insider
holders, fund holdings, symbol search/lookup, calendars, sector/industry overview, market
status, and the equity screener. Every capability here is served only for `US`/`GB`/`JP`/`EU`
(`sources/yfinance/_shared.py::_NON_TW_SUPPORTED`) — TW stays on FinMind/MOPS/gov adapters for
the equivalent concepts, so every input here restricts `market` to those four.

The screener tool (`run_equity_screener_us`) is deliberately **not** named `run_screener` to
avoid colliding with the design's own internal analytics screener (`analytics/`'s
gates->setup->rank funnel over the ingested store) — that internal screener is a distinct,
not-yet-built tool over a completely different input shape (`{market, preset|screen_type|
conditions[], ...}` per the gates/setup funnel) and would also be named `run_screener` if built;
namespacing this one `run_equity_screener_us` up front avoids a future naming collision rather
than reserving the generic name for whichever tool happens to land first.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dataaccess.router import DataAccessRouter
from dataaccess.screener import run_equity_screener as _run_equity_screener
from schemas.capabilities import (
    AnalystConsensus,
    EarningsCalendarRow,
    EconomicCalendarRow,
    EsgScore,
    FundHoldingRow,
    FundSectorWeightingRow,
    IndustryOverviewCompanyRow,
    InsiderActivityRow,
    InstitutionalHolderRow,
    IpoCalendarRow,
    MarketStatusInfo,
    OptionContractRow,
    SecFiling,
    SectorClassification,
    SectorOverviewCompanyRow,
    SymbolLookupResultRow,
    SymbolSearchResultRow,
)
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from schemas.screener import ScreenerQuery, ScreenerResult
from schemas.source import HealthStatus
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput

_NON_TW_MARKETS = frozenset({Market.US, Market.GB, Market.JP, Market.EU})
type _UntypedDict = dict[str, object]


def _validate_non_tw(market: Market) -> Market:
    if market not in _NON_TW_MARKETS:
        raise ValueError(
            f"{market.value} is not served by yfinance's non-TW capability set (US/GB/JP/EU only)"
        )
    return market


class NonTwSymbolInput(SymbolInput):
    """`SymbolInput` restricted to the four markets yfinance's non-TW-primary capability set
    actually covers."""

    @field_validator("market")
    @classmethod
    def _check_market(cls, value: Market) -> Market:
        return _validate_non_tw(value)


class _RowsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus


class GetOptionsChainInput(NonTwSymbolInput):
    expiry: str | None = Field(
        default=None, description="Option expiry date (ISO). Omitted means the nearest expiry."
    )


class GetOptionsChainOutput(_RowsOutput):
    contracts: tuple[OptionContractRow, ...] = Field(default_factory=tuple)


class GetOptionsChainTool(BaseTool):
    """`get_options_chain` — wraps `Capability.OPTIONS_CHAIN`."""

    name = "get_options_chain"
    description = "Get the option chain (calls + puts, one expiry) for one non-TW symbol."
    input_model: type[BaseModel] = GetOptionsChainInput
    output_model: type[BaseModel] = GetOptionsChainOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "options_chain"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetOptionsChainInput, args)
        params: dict[str, object] = {}
        if typed.expiry is not None:
            params["expiry"] = typed.expiry
        result, rows = await fetch_rows(
            self._router,
            Capability.OPTIONS_CHAIN,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=OptionContractRow,
            key="contracts",
            params=params,
        )
        return GetOptionsChainOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            contracts=rows,
        )


class GetInstitutionalHoldersOutput(_RowsOutput):
    holders: tuple[InstitutionalHolderRow, ...] = Field(default_factory=tuple)


class GetInstitutionalHoldersTool(BaseTool):
    """`get_institutional_holders` — wraps `Capability.INSTITUTIONAL_HOLDERS`."""

    name = "get_institutional_holders"
    description = "Get institutional + mutual-fund holder rows for one non-TW symbol."
    input_model: type[BaseModel] = NonTwSymbolInput
    output_model: type[BaseModel] = GetInstitutionalHoldersOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "institutional_holders"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(NonTwSymbolInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.INSTITUTIONAL_HOLDERS,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=InstitutionalHolderRow,
            key="holders",
        )
        return GetInstitutionalHoldersOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            holders=rows,
        )


class GetInsiderActivityOutput(_RowsOutput):
    transactions: tuple[InsiderActivityRow, ...] = Field(default_factory=tuple)


class GetInsiderActivityTool(BaseTool):
    """`get_insider_activity` — wraps `Capability.INSIDER_ACTIVITY` (US-style Form-4-derived
    disclosure; sparse/empty outside the US, never fabricated when absent)."""

    name = "get_insider_activity"
    description = "Get insider (Form-4-derived) transaction rows for one non-TW symbol."
    input_model: type[BaseModel] = NonTwSymbolInput
    output_model: type[BaseModel] = GetInsiderActivityOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "insider_activity"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(NonTwSymbolInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.INSIDER_ACTIVITY,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=InsiderActivityRow,
            key="transactions",
        )
        return GetInsiderActivityOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            transactions=rows,
        )


class GetAnalystConsensusOutput(_RowsOutput):
    consensus: AnalystConsensus | None = None


class GetAnalystConsensusTool(BaseTool):
    """`get_analyst_consensus` — wraps `Capability.ANALYST_CONSENSUS` (yfinance target-price/
    rating consensus). Unlike most tools in this module, this capability is real for **every**
    market including TW (`sources/yfinance/_shared.py::_TW_SUPPORTED`), so this tool takes a
    plain `SymbolInput`, not `NonTwSymbolInput` — a TW symbol is a legitimate, expected call,
    just a narrower payload shape (the `_extended` fields only populate for non-TW markets, per
    `AnalystConsensus`'s own docstring). No coverage (a symbol yfinance tracks no analysts for)
    is a graceful `consensus=None`, never fabricated."""

    name = "get_analyst_consensus"
    description = (
        "Get the analyst target-price/rating consensus for one symbol (any market). Returns "
        "no consensus (not an error) when yfinance tracks no analyst coverage for the symbol."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetAnalystConsensusOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "analyst_consensus"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result = await self._router.fetch(
            Capability.ANALYST_CONSENSUS, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        raw = result.payload.get("consensus")
        consensus = (
            AnalystConsensus.model_validate(cast(_UntypedDict, raw))
            if isinstance(raw, dict)
            else None
        )
        return GetAnalystConsensusOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            consensus=consensus,
        )


class GetEsgScoreOutput(_RowsOutput):
    esg: EsgScore | None = None


class GetEsgScoreTool(BaseTool):
    """`get_esg_score` — wraps `Capability.ESG` (yfinance Sustainalytics-derived ESG risk
    score). Real for every market including TW, same reasoning as `GetAnalystConsensusTool`.
    Sparse, large-cap-skewed coverage — no score is a graceful `esg=None`, never fabricated."""

    name = "get_esg_score"
    description = (
        "Get the ESG (environmental/social/governance) risk score for one symbol (any "
        "market). Returns no score (not an error) when yfinance tracks no ESG data for the "
        "symbol — coverage is sparse and skewed toward large-cap names."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetEsgScoreOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "esg_score"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result = await self._router.fetch(
            Capability.ESG, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        raw = result.payload.get("esg")
        esg = EsgScore.model_validate(cast(_UntypedDict, raw)) if isinstance(raw, dict) else None
        return GetEsgScoreOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            esg=esg,
        )


class UsSymbolInput(SymbolInput):
    """`SymbolInput` restricted to `Market.US` — for capabilities with no equivalent in any other
    market this codebase serves (e.g. SEC EDGAR filings), rather than the broader
    `NonTwSymbolInput` (US/GB/JP/EU)."""

    @field_validator("market")
    @classmethod
    def _check_market(cls, value: Market) -> Market:
        if value is not Market.US:
            raise ValueError(f"{value.value} is not served — this capability is US-only")
        return value


class GetSecFilingsOutput(_RowsOutput):
    filings: tuple[SecFiling, ...] = Field(default_factory=tuple)


class GetSecFilingsTool(BaseTool):
    """`get_sec_filings` — wraps `Capability.SEC_FILINGS` (yfinance's `Ticker.get_sec_filings()`,
    itself a read of SEC EDGAR). US-only: no equivalent regulatory-filing feed exists for any
    other market this codebase serves — TW's own filing-adjacent disclosures are
    `Capability.CORPORATE_ACTIONS`/`INSIDER_TRANSFER_DECLARATION` (MOPS), not this. No filings
    (a symbol with no EDGAR history, or a genuine fetch failure) is a graceful empty tuple, never
    fabricated."""

    name = "get_sec_filings"
    description = (
        "Get the most recent SEC EDGAR filings (10-K/10-Q/8-K/etc.) for one US-listed symbol. "
        "Returns an empty list (not an error) when no filings are found."
    )
    input_model: type[BaseModel] = UsSymbolInput
    output_model: type[BaseModel] = GetSecFilingsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "sec_filings"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(UsSymbolInput, args)
        result, filings = await fetch_rows(
            self._router,
            Capability.SEC_FILINGS,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=SecFiling,
            key="filings",
        )
        return GetSecFilingsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            filings=filings,
        )


class GetFundHoldingsOutput(_RowsOutput):
    holdings: tuple[FundHoldingRow, ...] = Field(default_factory=tuple)
    sector_weightings: tuple[FundSectorWeightingRow, ...] = Field(default_factory=tuple)


class GetFundHoldingsTool(BaseTool):
    """`get_fund_holdings` — wraps `Capability.FUND_HOLDINGS` (top holdings + sector
    weightings for one non-TW ETF/mutual fund; distinct from `get_etf_fund_info`'s TW-scoped,
    issuer-PCF-sourced fund metadata)."""

    name = "get_fund_holdings"
    description = "Get top-holdings + sector-weighting rows for one non-TW ETF/mutual fund."
    input_model: type[BaseModel] = NonTwSymbolInput
    output_model: type[BaseModel] = GetFundHoldingsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "fund_holdings"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(NonTwSymbolInput, args)
        result = await self._router.fetch(
            Capability.FUND_HOLDINGS, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        raw_holdings = result.payload.get("holdings")
        raw_weightings = result.payload.get("sector_weightings")
        holdings = (
            tuple(FundHoldingRow.model_validate(row) for row in cast("list[object]", raw_holdings))
            if isinstance(raw_holdings, list)
            else ()
        )
        weightings = (
            tuple(
                FundSectorWeightingRow.model_validate(row)
                for row in cast("list[object]", raw_weightings)
            )
            if isinstance(raw_weightings, list)
            else ()
        )
        return GetFundHoldingsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            holdings=holdings,
            sector_weightings=weightings,
        )


class SymbolSearchInput(BaseModel):
    """No `id` field — `symbol_search`/`symbol_lookup` take a free-text query, not a symbol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1)
    market: Market

    @field_validator("market")
    @classmethod
    def _check_market(cls, value: Market) -> Market:
        return _validate_non_tw(value)

    def to_symbol(self) -> Symbol:
        return Symbol(id="", market=self.market)


class GetSymbolSearchOutput(_RowsOutput):
    results: tuple[SymbolSearchResultRow, ...] = Field(default_factory=tuple)


class GetSymbolSearchTool(BaseTool):
    """`get_symbol_search` — wraps `Capability.SYMBOL_SEARCH` (`yfinance.Search`; free-text
    name/ticker resolution, e.g. "台積電"/"TSMC" -> 2330.TW)."""

    name = "get_symbol_search"
    description = (
        "Search for a symbol by free-text company name or ticker (multi-market resolution)."
    )
    input_model: type[BaseModel] = SymbolSearchInput
    output_model: type[BaseModel] = GetSymbolSearchOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolSearchInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.SYMBOL_SEARCH,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=SymbolSearchResultRow,
            key="results",
            params={"query": typed.query},
        )
        return GetSymbolSearchOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            results=rows,
        )


class GetSymbolLookupOutput(_RowsOutput):
    results: tuple[SymbolLookupResultRow, ...] = Field(default_factory=tuple)


class GetSymbolLookupTool(BaseTool):
    """`get_symbol_lookup` — wraps `Capability.SYMBOL_LOOKUP` (`yfinance.Lookup`; scoped to
    one instrument type)."""

    name = "get_symbol_lookup"
    description = (
        "Look up symbols by free-text query, scoped to one instrument type (stock/ETF/index/...)."
    )
    input_model: type[BaseModel] = SymbolSearchInput
    output_model: type[BaseModel] = GetSymbolLookupOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolSearchInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.SYMBOL_LOOKUP,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=SymbolLookupResultRow,
            key="results",
            params={"query": typed.query},
        )
        return GetSymbolLookupOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            results=rows,
        )


class MarketScopedInput(BaseModel):
    """No per-symbol scope — calendars/market-status are market-wide feeds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market

    @field_validator("market")
    @classmethod
    def _check_market(cls, value: Market) -> Market:
        return _validate_non_tw(value)

    def to_symbol(self) -> Symbol:
        return Symbol(id="", market=self.market)


class GetCalendarEventsFeedOutput(BaseModel):
    """Bundles earnings/IPO/economic calendars into one report — a distinct, narrower
    yfinance-native feed bundle than a possible future `get_calendar_events` analytics-layer
    composite over `calendar/`; that higher-level tool (if/when built) could draw on this one
    as one of its own inputs rather than duplicating this fetch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    currentness_caveat: str = Field(min_length=1)
    earnings: tuple[EarningsCalendarRow, ...] = Field(default_factory=tuple)
    ipo: tuple[IpoCalendarRow, ...] = Field(default_factory=tuple)
    economic: tuple[EconomicCalendarRow, ...] = Field(default_factory=tuple)


class GetCalendarEventsFeedTool(BaseTool):
    """`get_calendar_events_feed` — bundles `Capability.EARNINGS_CALENDAR` + `Capability.
    IPO_CALENDAR` + `Capability.ECONOMIC_CALENDAR` (`yfinance.Calendars`, market-wide feeds)."""

    name = "get_calendar_events_feed"
    description = (
        "Get upcoming earnings, IPO, and macro-economic calendar events for one non-TW market."
    )
    input_model: type[BaseModel] = MarketScopedInput
    output_model: type[BaseModel] = GetCalendarEventsFeedOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketScopedInput, args)
        symbol = typed.to_symbol()
        earnings_result, earnings_rows = await fetch_rows(
            self._router,
            Capability.EARNINGS_CALENDAR,
            symbol,
            tls_policy=ctx.tls_policy,
            model=EarningsCalendarRow,
            key="rows",
        )
        _ipo_result, ipo_rows = await fetch_rows(
            self._router,
            Capability.IPO_CALENDAR,
            symbol,
            tls_policy=ctx.tls_policy,
            model=IpoCalendarRow,
            key="rows",
        )
        _economic_result, economic_rows = await fetch_rows(
            self._router,
            Capability.ECONOMIC_CALENDAR,
            symbol,
            tls_policy=ctx.tls_policy,
            model=EconomicCalendarRow,
            key="rows",
        )
        return GetCalendarEventsFeedOutput(
            currentness_caveat=earnings_result.currentness_caveat,
            earnings=earnings_rows,
            ipo=ipo_rows,
            economic=economic_rows,
        )


class GetSectorOverviewInput(MarketScopedInput):
    sector_key: str = Field(min_length=1)


class GetSectorOverviewOutput(_RowsOutput):
    rows: tuple[SectorOverviewCompanyRow, ...] = Field(default_factory=tuple)


class GetSectorOverviewTool(BaseTool):
    """`get_sector_overview` — wraps `Capability.SECTOR_OVERVIEW` (`yfinance.Sector`)."""

    name = "get_sector_overview"
    description = "Get top companies/ETFs/mutual funds for one Yahoo sector aggregate."
    input_model: type[BaseModel] = GetSectorOverviewInput
    output_model: type[BaseModel] = GetSectorOverviewOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "sector_overview"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetSectorOverviewInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.SECTOR_OVERVIEW,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=SectorOverviewCompanyRow,
            key="rows",
            params={"sector_key": typed.sector_key},
        )
        return GetSectorOverviewOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetIndustryOverviewInput(MarketScopedInput):
    industry_key: str = Field(min_length=1)


class GetIndustryOverviewOutput(_RowsOutput):
    rows: tuple[IndustryOverviewCompanyRow, ...] = Field(default_factory=tuple)


class GetIndustryOverviewTool(BaseTool):
    """`get_industry_overview` — wraps `Capability.INDUSTRY_OVERVIEW` (`yfinance.Industry`)."""

    name = "get_industry_overview"
    description = (
        "Get top companies for one Yahoo industry aggregate (one level narrower than sector)."
    )
    input_model: type[BaseModel] = GetIndustryOverviewInput
    output_model: type[BaseModel] = GetIndustryOverviewOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "industry_overview"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetIndustryOverviewInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.INDUSTRY_OVERVIEW,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=IndustryOverviewCompanyRow,
            key="rows",
            params={"industry_key": typed.industry_key},
        )
        return GetIndustryOverviewOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetStockPeersOutput(BaseModel):
    """Composite output over two capability fetches (`SECTOR_CLASS` resolve, then
    `SECTOR_OVERVIEW`/`INDUSTRY_OVERVIEW`) — `source_id`/`currentness_caveat`/`health_status`
    describe the peer-list fetch specifically (the caveat that actually matters to a reader of
    the peer rows); `sector`/`industry` display names are carried through for the panel's own
    header, without requiring a second round trip to `get_sector_class`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    sector: str | None = None
    industry: str | None = None
    sector_peers: tuple[SectorOverviewCompanyRow, ...] = Field(default_factory=tuple)
    industry_peers: tuple[IndustryOverviewCompanyRow, ...] = Field(default_factory=tuple)
    unavailable_reason: str | None = None


class GetStockPeersTool(BaseTool):
    """`get_stock_peers` — resolves one symbol's sector/industry slug key (`Capability.
    SECTOR_CLASS`, `sector_key`/`industry_key`) then fetches its peer companies (`Capability.
    SECTOR_OVERVIEW`/`INDUSTRY_OVERVIEW`, `yfinance.Sector`/`Industry`) in one call — sparing the
    frontend a symbol -> sector_key resolve round trip before it can render a peer-overview
    panel. Genuinely unavailable (never fabricated) when the symbol carries no sector/industry
    classification at all (sparse for some non-US/small-cap names)."""

    name = "get_stock_peers"
    description = (
        "Get one symbol's sector/industry peer companies — resolves the symbol's own sector/"
        "industry classification, then returns the top companies in each. Public data, any "
        "market yfinance classifies."
    )
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetStockPeersOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "stock_peers"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        sym = typed.to_symbol()
        class_result = await self._router.fetch(
            Capability.SECTOR_CLASS, sym, tls_policy=ctx.tls_policy
        )
        raw_classification = class_result.payload.get("classification")
        classification = (
            SectorClassification.model_validate(cast(_UntypedDict, raw_classification))
            if isinstance(raw_classification, dict)
            else None
        )
        if classification is None or (
            classification.sector_key is None and classification.industry_key is None
        ):
            return GetStockPeersOutput(
                source_id=class_result.source_id,
                currentness_caveat=class_result.currentness_caveat,
                health_status=class_result.health.status,
                unavailable_reason="no sector/industry classification for this symbol",
            )
        sector_peers: tuple[SectorOverviewCompanyRow, ...] = ()
        industry_peers: tuple[IndustryOverviewCompanyRow, ...] = ()
        peers_result = class_result
        if classification.sector_key:
            peers_result, sector_peers = await fetch_rows(
                self._router,
                Capability.SECTOR_OVERVIEW,
                sym,
                tls_policy=ctx.tls_policy,
                model=SectorOverviewCompanyRow,
                key="rows",
                params={"sector_key": classification.sector_key},
            )
        if classification.industry_key:
            peers_result, industry_peers = await fetch_rows(
                self._router,
                Capability.INDUSTRY_OVERVIEW,
                sym,
                tls_policy=ctx.tls_policy,
                model=IndustryOverviewCompanyRow,
                key="rows",
                params={"industry_key": classification.industry_key},
            )
        return GetStockPeersOutput(
            source_id=peers_result.source_id,
            currentness_caveat=peers_result.currentness_caveat,
            health_status=peers_result.health.status,
            sector=classification.sector,
            industry=classification.industry,
            sector_peers=sector_peers,
            industry_peers=industry_peers,
        )


class GetMarketStatusOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    status: MarketStatusInfo | None = None


class GetMarketStatusTool(BaseTool):
    """`get_market_status` — wraps `Capability.MARKET_STATUS` (`yfinance.Market` open/closed +
    summary)."""

    name = "get_market_status"
    description = "Get the open/closed status + summary for one non-TW market region."
    input_model: type[BaseModel] = MarketScopedInput
    output_model: type[BaseModel] = GetMarketStatusOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketScopedInput, args)
        result = await self._router.fetch(
            Capability.MARKET_STATUS, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        raw_status = result.payload.get("status")
        status = (
            MarketStatusInfo.model_validate(cast(_UntypedDict, raw_status))
            if isinstance(raw_status, dict)
            else None
        )
        return GetMarketStatusOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            status=status,
        )


class RunEquityScreenerUsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result: ScreenerResult


class RunEquityScreenerUsTool(BaseTool):
    """`run_equity_screener_us` — wraps `sources.yfinance.run_screener` via the
    `dataaccess.screener` seam (a query-shaped carve-out, not a `(capability, symbol)` fetch —
    see that module's docstring). Named to avoid colliding with a future internal
    gates->setup->rank analytics screener (see this module's own docstring for the naming
    rationale); Yahoo's screener coverage is validated primarily for US, though the query
    itself is not intrinsically country-locked (GB/JP/EU-only queries are unverified by this
    pass, per `sources/yfinance/screener.py`'s own finding)."""

    name = "run_equity_screener_us"
    description = (
        "Run a custom equity/ETF/fund screener query (AND/OR field conditions) against "
        "Yahoo's screener endpoint and return matching symbols. Public/sponsor per criteria "
        "used is not separately gated here — this tool itself is public."
    )
    input_model: type[BaseModel] = ScreenerQuery
    output_model: type[BaseModel] = RunEquityScreenerUsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "equity_screener"

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(ScreenerQuery, args)
        result = await _run_equity_screener(typed)
        return RunEquityScreenerUsOutput(result=result)


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [
        (GetOptionsChainTool(router), False),
        (GetInstitutionalHoldersTool(router), False),
        (GetInsiderActivityTool(router), False),
        (GetAnalystConsensusTool(router), False),
        (GetEsgScoreTool(router), False),
        (GetFundHoldingsTool(router), False),
        (GetSymbolSearchTool(router), False),
        (GetSymbolLookupTool(router), False),
        (GetCalendarEventsFeedTool(router), False),
        (GetSectorOverviewTool(router), False),
        (GetIndustryOverviewTool(router), False),
        (GetStockPeersTool(router), False),
        (GetMarketStatusTool(router), False),
        (GetSecFilingsTool(router), False),
        (RunEquityScreenerUsTool(), False),
    ]


__all__ = [
    "GetAnalystConsensusOutput",
    "GetAnalystConsensusTool",
    "GetCalendarEventsFeedOutput",
    "GetCalendarEventsFeedTool",
    "GetEsgScoreOutput",
    "GetEsgScoreTool",
    "GetFundHoldingsOutput",
    "GetFundHoldingsTool",
    "GetIndustryOverviewInput",
    "GetIndustryOverviewOutput",
    "GetIndustryOverviewTool",
    "GetInsiderActivityOutput",
    "GetInsiderActivityTool",
    "GetInstitutionalHoldersOutput",
    "GetInstitutionalHoldersTool",
    "GetMarketStatusOutput",
    "GetMarketStatusTool",
    "GetOptionsChainInput",
    "GetOptionsChainOutput",
    "GetOptionsChainTool",
    "GetSecFilingsOutput",
    "GetSecFilingsTool",
    "GetSectorOverviewInput",
    "GetSectorOverviewOutput",
    "GetSectorOverviewTool",
    "GetStockPeersOutput",
    "GetStockPeersTool",
    "GetSymbolLookupOutput",
    "GetSymbolLookupTool",
    "GetSymbolSearchOutput",
    "GetSymbolSearchTool",
    "MarketScopedInput",
    "NonTwSymbolInput",
    "RunEquityScreenerUsOutput",
    "RunEquityScreenerUsTool",
    "SymbolSearchInput",
    "UsSymbolInput",
    "build_registration_table",
]
