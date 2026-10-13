"""yfinance-native capabilities (non-TW primary; no FinMind/gov equivalent)."""

from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from schemas.enums import Market

# --- yfinance-native capabilities (non-TW primary; no existing FinMind/gov equivalent) ---


class OptionContractRow(BaseModel):
    """`options_chain` — one option contract row (one strike, one side) for one expiry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # the underlying
    market: Market
    expiry: date_
    contract_symbol: str
    side: str  # "call" | "put"
    strike: float
    last_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    implied_volatility: float | None = None
    in_the_money: bool = False


class InstitutionalHolderRow(BaseModel):
    """`institutional_holders` — one institutional or mutual-fund holder row (US-style 13F-
    derived disclosure; `holder_type` distinguishes the two source tables)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    holder_type: str  # "institutional" | "mutual_fund"
    holder_name: str
    date_reported: date_
    shares: int | None = None
    value: float | None = None
    pct_held: float | None = None
    pct_change: float | None = None


class InsiderActivityRow(BaseModel):
    """`insider_activity` — one insider (Form-4-derived) transaction row. US-style disclosure;
    sparse/empty for non-US markets — never fabricated when absent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    insider_name: str
    position: str | None = None
    transaction: str | None = None
    shares: int | None = None
    value: float | None = None
    start_date: date_ | None = None
    ownership: str | None = None  # "D" direct / "I" indirect


class SymbolSearchResultRow(BaseModel):
    """`symbol_search` — one `yfinance.Search` quote match for a free-text query (e.g.
    "台積電"/"TSMC"→2330.TW; general multi-market name/ticker resolution)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    symbol: str
    exchange: str
    short_name: str | None = None
    long_name: str | None = None
    quote_type: str | None = None
    sector: str | None = None
    industry: str | None = None
    score: float | None = None


class SymbolLookupResultRow(BaseModel):
    """`symbol_lookup` — one `yfinance.Lookup` roster row for a query, scoped to one instrument
    type (stock/ETF/index/future/currency/crypto/mutual fund)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    symbol: str
    instrument_type: str  # "stock" | "etf" | "index" | "future" | "currency" | "cryptocurrency"
    exchange: str | None = None
    short_name: str | None = None


class SymbolDirectorySearchRow(BaseModel):
    """`symbol_directory_search` — one ranked match from the ingested `TaiwanStockInfo` roster
    (`StockInfoRow`) for a free-text symbol/name query. `instrument_kind` is derived from the
    symbol's code shape (`analytics.symbol_universe.classify_symbol_type`), collapsing
    leveraged/inverse ETFs into `"etf"` — this is a UI-facing search-result row, not the roster
    row itself, so it carries only what a type-ahead result needs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    name: str
    instrument_kind: Literal["stock", "etf"]
    industry_category: str | None = None


class EarningsCalendarRow(BaseModel):
    """`earnings_calendar` — one upcoming/recent earnings-report event row (`yfinance.Calendars`
    market-wide feed, not scoped to one symbol's own `.calendar`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    symbol: str
    company: str
    event_start: datetime
    timing: str | None = None  # e.g. "Time Not Supplied", "Before Market Open"
    eps_estimate: float | None = None
    reported_eps: float | None = None
    surprise_pct: float | None = None


class IpoCalendarRow(BaseModel):
    """`ipo_calendar` — one upcoming/recent IPO/offering row (`yfinance.Calendars`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    symbol: str
    company: str
    exchange: str | None = None
    filing_date: date_ | None = None
    offer_date: date_ | None = None
    price_from: float | None = None
    price_to: float | None = None
    price: float | None = None
    currency: str | None = None
    shares: float | None = None
    action: str | None = None  # e.g. "Expected", "Priced", "Withdrawn"


class EconomicCalendarRow(BaseModel):
    """`economic_calendar` — one macro-release event row (`yfinance.Calendars`); modeled
    `Market.GLOBAL` since the underlying feed spans multiple countries/regions, distinguished
    only by the free-text `region` field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    event: str
    region: str | None = None
    event_time: datetime
    for_period: str | None = None
    actual: float | None = None
    expected: float | None = None
    last: float | None = None
    revised: float | None = None


class MacroReleaseEventRow(BaseModel):
    """`macro_calendar` — one upcoming/recent macro-release event row
    (`financecalendar.com`'s `wp-json/fc/v1/calendar` feed, a distinct third-party source from
    `EconomicCalendarRow`'s yfinance `Calendars` feed — see `sources/financecalendar.py`'s own
    module docstring for why this is additive, not a replacement). Modeled `Market.GLOBAL`: the
    feed spans multiple countries (US GDP/CPI/NFP, RBA/ECB/BOJ rate decisions, Australia/Canada/
    Eurozone releases, ...), distinguished only by the free-text `category`/`name` fields, the
    same "no single-country home" reasoning `EconomicCalendarRow` already uses.

    `consensus`/`prior`/`actual` are deliberately `str | None`, never `float` — the live upstream
    returns human-readable strings ("Hold at 3.50-3.75%", "3.8% annual", "55.6") that mix ranges,
    units, and plain numbers; coercing them to a float would either fail or silently misrepresent
    a range/unit. Render as opaque cited text, never parsed or computed on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    date: date_
    time_utc: datetime | None = None
    name: str
    title: str
    impact: str  # "high" | "medium" | "low", upstream's own vocabulary
    category: str
    consensus: str | None = None
    prior: str | None = None
    actual: str | None = None
    url: str | None = None


class SectorOverviewCompanyRow(BaseModel):
    """`sector_overview` — one top-company/top-ETF/top-mutual-fund row within one
    `yfinance.Sector` aggregate view. `row_kind` distinguishes the three sub-lists this
    capability folds together rather than splitting into three near-duplicate capabilities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    sector_key: str
    row_kind: str  # "company" | "etf" | "mutual_fund"
    symbol: str
    name: str
    rating: str | None = None  # top_companies only, e.g. "Strong Buy"
    market_weight: float | None = None  # top_companies only


class IndustryOverviewCompanyRow(BaseModel):
    """`industry_overview` — one top-company row within one `yfinance.Industry` aggregate
    view (analogous to `sector_overview`, one level narrower).

    `ytd_return`/`last_price`/`target_price` come from `yfinance.Industry.top_performing_
    companies` (only populated on a `row_kind="performing_company"` row); `ytd_return`/
    `growth_estimate` come from `top_growth_companies` (`row_kind="growth_company"` only). A
    plain `row_kind="company"` row (from `top_companies`, the same frame `sector_overview` reads)
    never carries any of these four — left `None`, never fabricated from the other frames."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    industry_key: str
    sector_key: str
    row_kind: str  # "company" | "growth_company" | "performing_company"
    symbol: str
    name: str
    rating: str | None = None
    market_weight: float | None = None
    ytd_return: float | None = None
    last_price: float | None = None
    target_price: float | None = None
    growth_estimate: float | None = None


class SectorOverviewStats(BaseModel):
    """`sector_overview`'s aggregate-level stats — `yfinance.Sector.overview`'s own dict, mapped
    1:1. Distinct from `SectorOverviewCompanyRow` (a per-company row): this is the ONE summary
    for the whole sector aggregate itself, not a company within it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    sector_key: str
    companies_count: int | None = None
    market_cap: float | None = None
    market_weight: float | None = None
    employee_count: int | None = None
    description: str | None = None


class SectorIndustryWeightRow(BaseModel):
    """One row of `yfinance.Sector.industries` — this sector's own industry breakdown by market
    weight (e.g. Technology -> Semiconductors 38%, Software-Infrastructure 21%, ...). The data
    source for a sector-industries composition treemap; distinct from `IndustryOverviewCompanyRow`
    (a company within ONE industry) — this is industries within ONE sector."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    sector_key: str
    industry_key: str
    name: str
    market_weight: float


class MarketStatusInfo(BaseModel):
    """`market_status` — `yfinance.Market` open/closed status + summary for one market region.
    Market-level, no symbol scope; `region` is the yfinance `MarketRegion` code (US/GB/ASIA/
    EUROPE/...), not our `Market` enum, since yfinance's own region taxonomy doesn't map
    1:1 onto ours (e.g. `ASIA` spans both `Market.JP` and others)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    region: str
    status: str  # e.g. "open" | "closed" | "pre" | "post"
    message: str | None = None
    open_at: datetime | None = None
    close_at: datetime | None = None


class FundHoldingRow(BaseModel):
    """`fund_holdings` — one top-holding constituent row of one ETF/mutual fund
    (`yfinance.scrapers.funds.FundsData.top_holdings`). Distinct from `etf_holdings`
    (issuer-PCF-sourced, TW-scoped, fund-level metadata only) — this is yfinance's own
    holdings-weight slice, relevant for non-TW ETFs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # the fund itself
    market: Market
    holding_symbol: str
    holding_name: str
    weight: float


class FundSectorWeightingRow(BaseModel):
    """`fund_holdings` (sector-weighting slice) — one sector-weight row for one ETF/mutual
    fund (`FundsData.sector_weightings`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # the fund itself
    market: Market
    sector: str
    weight: float


class FundProfileRow(BaseModel):
    """`fund_profile` — one non-TW ETF/mutual fund's identity + summary-return fields from
    yfinance's own `.info` payload (`sources/yfinance/analyst.py::_fund_profile`). The 總覽
    tab's fund-equivalent of `GetCompanyProfileOutput` for a plain equity — a fund has no
    chairman/EPS/capital, but has real AUM/category/issuer/return figures `ProfileCard` never
    surfaced. Every field degrades to `None` independently (never fabricated) when yfinance's
    own `.info` payload doesn't carry it for this symbol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    total_assets: float | None = None  # totalAssets/netAssets — AUM
    category: str | None = None  # e.g. "Large Growth"
    fund_family: str | None = None  # issuer, e.g. "Invesco"
    ytd_return_pct: float | None = None  # already a percent (yfinance's own `ytdReturn` scale)
    distribution_yield_pct: float | None = None  # yfinance's own `yield`, scaled to a percent
    inception_date: date_ | None = None
