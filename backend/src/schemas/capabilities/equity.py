"""Per-symbol equity facts: bars, dividends, statements, news, filings, active ETFs."""

from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import Market


class EODBar(BaseModel):
    """`price_eod` — one end-of-day bar."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float | None = None


class DividendEvent(BaseModel):
    """`dividends` — one dividend policy/ex-dividend record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    announced_date: date_
    fiscal_year: str
    cash_dividend: float | None = None
    stock_dividend: float | None = None
    cash_ex_dividend_date: date_ | None = None
    stock_ex_dividend_date: date_ | None = None
    # FinMind's own `CashDividendPaymentDate` (股利發放日) — when the cash distribution actually
    # lands, distinct from `cash_ex_dividend_date` (除息日). `None` when FinMind has not yet
    # published a payment date for this event (common for a recently-announced dividend whose
    # ex-date has passed but payment date hasn't been confirmed yet), never fabricated as
    # ex-date + N days.
    cash_dividend_payment_date: date_ | None = None


class FinancialStatementLine(BaseModel):
    """`financials` — one line item from an income/balance/cash-flow statement. `period_type`
    defaults to `"quarterly"` — FinMind (TW's source for this capability) reports one frequency
    only, so every existing TW row is implicitly quarterly; yfinance (the US/non-TW source)
    reports annual/quarterly/TTM as genuinely distinct frames, so US rows set this field for
    real rather than leaving every US row ambiguously defaulted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    period_end: date_
    statement_type: str  # e.g. "income", "balance_sheet", "cash_flow"
    period_type: Literal["annual", "quarterly", "ttm"] = "quarterly"
    item: str  # normalized line-item type (upstream "type" column)
    origin_name: str  # upstream label, kept for citation/traceability
    value: float


class InstitutionalFlowRow(BaseModel):
    """`institutional_flow` — one institutional-investor buy/sell row for one trading day."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    investor: str  # e.g. "Foreign_Investor", "Investment_Trust", "Dealer_self"
    buy: int
    sell: int


class ChipBrokerRow(BaseModel):
    """`chip_broker` — one branch(分點)/broker trading row for one trading day. Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    broker_id: str
    broker_name: str
    price: float
    buy: int
    sell: int


class AnalystConsensus(BaseModel):
    """`analyst_consensus` — the licensed analyst-ratings/target-price gap (yfinance).

    The `_extended` fields are populated only for non-TW markets, where yfinance's analyst
    coverage is primary rather than a sparse gap-filler — TW payloads never set them, so the
    existing narrow TW shape is unaffected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    mean_target: float | None = None
    median_target: float | None = None
    high_target: float | None = None
    low_target: float | None = None
    number_of_analysts: int = Field(default=0, ge=0)
    recommendation_key: str | None = None  # e.g. "buy" — descriptive third-party datum only
    strong_buy: int | None = None  # recommendations_summary current-period tally
    buy: int | None = None
    hold: int | None = None
    sell: int | None = None
    strong_sell: int | None = None
    latest_upgrade_downgrade_firm: str | None = None  # most recent upgrades_downgrades row
    latest_upgrade_downgrade_action: str | None = None  # e.g. "up"/"down"/"main"/"init"
    forward_eps_estimate: float | None = None  # earnings_estimate current-fiscal-year avg
    forward_revenue_estimate: float | None = None  # revenue_estimate current-fiscal-year avg
    long_term_growth_estimate: float | None = None  # growth_estimates LTG stockTrend
    # EPS-revision/growth depth — yfinance-only, same `_extended`-field convention as the block
    # above (never populated for TW): eps_trend's current-fiscal-year estimate as it stood today
    # vs 7/30/60/90 days ago, eps_revisions' analyst up/down-revision counts, and this-year/
    # next-year growth-estimate percentages (vs `long_term_growth_estimate`'s multi-year LTG).
    eps_trend_current: float | None = None  # eps_trend 0y "current"
    eps_trend_7_days_ago: float | None = None
    eps_trend_30_days_ago: float | None = None
    eps_trend_60_days_ago: float | None = None
    eps_trend_90_days_ago: float | None = None
    eps_revisions_up_last_30_days: int | None = None  # eps_revisions 0y upLast30days
    eps_revisions_down_last_30_days: int | None = None  # eps_revisions 0y downLast30days
    current_year_growth_estimate: float | None = None  # growth_estimates 0y stockTrend
    next_year_growth_estimate: float | None = None  # growth_estimates +1y stockTrend


class EsgScore(BaseModel):
    """`esg` — Sustainalytics-derived ESG risk score (large-cap only, sparse coverage)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    total_esg_score: float | None = None
    esg_risk_percentile: float | None = None


class SectorClassification(BaseModel):
    """`sector_class` — English cross-market sector/industry label (yfinance); FinMind's
    Chinese 產業別/產業鏈 remains authoritative for TW. `sector_key`/`industry_key` are yfinance's
    own slug identifiers (e.g. "technology"), distinct from `sector`/`industry`'s human-readable
    display names — the slugs are what `Capability.SECTOR_OVERVIEW`/`INDUSTRY_OVERVIEW` (backed
    by `yfinance.Sector`/`Industry`) actually key on, so a caller resolving "this symbol's peer
    group" needs the slug, not the display name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    sector: str | None = None
    industry: str | None = None
    sector_key: str | None = None
    industry_key: str | None = None


class NewsItem(BaseModel):
    """`news` — one normalized news item from RSS/Finnhub. Third-party content: cite, never
    restate as our own analysis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    link: str
    source: str
    published_at: datetime
    summary: str | None = None


class SecFiling(BaseModel):
    """`sec_filings` — one SEC EDGAR filing row for a listed US company (yfinance's
    `Ticker.get_sec_filings()`). Third-party regulatory-filing content: `title`/`type` are the
    filing's own metadata, never summarized into a trading signal. US-only — no equivalent feed
    exists for any other market this codebase serves."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    filing_date: date_
    filing_type: str  # e.g. "10-K", "10-Q", "8-K", "4"
    title: str
    edgar_url: str | None = None  # link to the filing on SEC EDGAR, when yfinance provides one


class PttPost(BaseModel):
    """One PTT board-index listing row — title/author/push-count/tag only (MVP scope: the
    index listing, not full post bodies/comments). `board_tag` of `offtopic` marks
    Beauty/Gossiping so downstream guardrails can exclude it from any market claim; `unverified
    retail opinion` boards (Stock/Tech_Job/Warrant) carry `stock_buzz`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    board: str
    board_tag: str  # "stock_buzz" (Stock/Tech_Job/Warrant) | "offtopic" (Beauty/Gossiping)
    title: str
    post_tag: str | None = None  # e.g. "[標的]"/"[新聞]"/"[心得]" parsed from the title
    author: str
    push_count: str  # raw upstream token: an int-like string, "爆", or "X87" (net negative)
    link: str
    posted_date: str | None = None  # ISO "YYYY-MM-DD", resolved from the index's bare "M/D" —
    # None only if that resolution itself fails (never fabricated)


class MaterialAnnouncement(BaseModel):
    """`corporate_actions` — one 重大訊息 (material announcement) disclosure row. Third-party
    regulatory-filing content: `subject`/`detail` remain free Chinese text (that is the actual
    content of the filing), never summarized or translated into a trading signal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    company_name: str
    announced_date: date_
    announced_time: str  # "HH:MM:SS", converted from the raw digit-string time field
    subject: str
    detail: str
    qualifying_clause: str | None = None  # upstream "符合條款" — which disclosure rule triggered


class DirectorShareholding(BaseModel):
    """`shareholding` — one 董監事持股餘額 (director/supervisor shareholding) row for one
    reporting month."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: str  # reporting year-month, ISO "YYYY-MM" (upstream "資料年月" is ROC year-month)
    person_name: str
    title: str  # upstream "職稱", e.g. "董事長本人", "董事之法人代表人"
    shares_at_election: int | None = None
    current_shares: int | None = None
    pledged_shares: int | None = None
    pledged_ratio: str | None = None  # upstream keeps this as a percentage-string, e.g. "0.00%"


class MaterialAnnouncementRow(BaseModel):
    """`corporate_actions` — one 重大訊息 row from the WHOLE-MARKET dump (ingest-owned, one
    partition per day), distinct from `MaterialAnnouncement` (the per-symbol live-fetch shape
    `get_material_announcements` still serves). `category` is a derived bucket computed at
    ingest time (`sources/mops.py::_announcement_category`) from `qualifying_clause` — TWSE's own
    disclosure-rule clause citation (e.g. "第51款"), meaningless to a reader as a raw number, so
    never exposed un-bucketed to a whole-market list view. `qualifying_clause` itself is kept
    alongside `category` rather than dropped, for anyone who wants the raw citation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    company_name: str
    announced_date: date_
    announced_time: str  # "HH:MM:SS"
    subject: str
    detail: str
    qualifying_clause: str | None = None
    category: str


class InsiderTransferDeclarationRow(BaseModel):
    """`insider_transfer_declaration` — one 內部人股權轉讓事前申報 (insider advance-transfer-plan
    declaration) row from TWSE MOPS's `t187ap12_L` whole-market dump. Advance notice of an
    intended insider share transfer/sale, filed before it happens — distinct from
    `DirectorShareholding` (a monthly holdings snapshot, not an event) and from
    `Capability.INSIDER_ACTIVITY` (yfinance/US Form-4-derived, never TW).

    Scoped to `t187ap12_L` only. `t187ap13_L` (a related but distinct "reason not transferred"
    filing) is intentionally not covered by this model — a real, separate MOPS dataset, deferred
    rather than silently dropped."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    company_name: str
    filer_role: str  # upstream "申報人身分"
    person_name: str  # upstream "姓名"
    transfer_method: str  # upstream "預定轉讓方式及股數-轉讓方式"
    transfer_shares: int  # upstream "預定轉讓方式及股數-轉讓股數"
    max_daily_exchange_shares: int | None = None  # upstream "每日於盤中交易最大得轉讓股數"
    transferee: str | None = None  # upstream "受讓人" — set for a 贈與, blank for open-market sale
    current_shares_own: int  # upstream "目前持有股數-自有持股"
    current_shares_trust: int  # upstream "目前持有股數-保留運用決定權信託股數"
    planned_shares_own: int  # upstream "預定轉讓總股數-自有持股"
    planned_shares_trust: int  # upstream "預定轉讓總股數-保留運用決定權信託股數"
    projected_shares_own_after: int  # upstream "預定轉讓後持股-自有持股"
    projected_shares_trust_after: int  # upstream "預定轉讓後持股-保留運用決定權信託股數"
    transfer_window_start: date_  # upstream "有效轉讓期間", first half of "YYYMMDD~YYYMMDD"
    transfer_window_end: date_  # upstream "有效轉讓期間", second half


class ActiveEtfInfoRow(BaseModel):
    """`active_etf_info` — one active-ETF roster row (free tier; list/basic info only)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    name: str
    category: str  # domestic / foreign
    fund_type: str  # twse / tpex


class ActiveEtfHoldingRow(BaseModel):
    """`active_etf_holding` — one constituent row of one active ETF's daily holding snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # the ETF itself
    market: Market
    trade_date: date_
    component_stock_id: str
    component_stock_name: str
    asset_type: str
    shares: int
    weight: float
    market_value: float
    currency: str


class ActiveEtfHoldingChangeRow(BaseModel):
    """`active_etf_holding_change` — FinMind's own pre-diffed buy/sell of one constituent of
    one active ETF for one trading day. Exactly one of `buy`/`sell` is non-zero per row —
    passed through as-is, never re-diffed here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # the ETF itself
    market: Market
    trade_date: date_
    component_stock_id: str
    component_stock_name: str
    buy: int
    sell: int


class StockInfoRow(BaseModel):
    """`stock_info` / `stock_info_with_warrant` — one roster row of the Taiwan stock/warrant
    universe reference table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    name: str
    industry_category: str
    security_type: str  # upstream "type"
    as_of: date_


class WarrantUnderlyingRow(BaseModel):
    """`warrant_underlying` — one warrant-code-to-underlying mapping interval. A warrant code
    is recycled across issues, so the same `symbol` can appear in multiple rows, each scoped to
    its own `[listed_date, end_date]` interval — resolve a code's underlying on a given day by
    picking the row whose interval contains that day."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # the warrant code
    market: Market
    listed_date: date_
    close: float
    target_stock_id: str  # the underlying (mother stock)
    target_close: float
    warrant_type: str
    fulfillment_method: str
    end_date: date_
    fulfillment_start_date: date_ | None = None
    fulfillment_end_date: date_ | None = None
    exercise_ratio: float
    fulfillment_price: float
