"""Normalized per-capability output models.

Callers request a capability, never a source, and get the same shape regardless of which
adapter served it. Each model is immutable — canonical facts are never
mutated — and carries only normalized, typed fields — no raw upstream payloads leak through.
"""

from __future__ import annotations

from datetime import date as date_
from datetime import datetime

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
    period_type: str = "quarterly"  # "annual" | "quarterly" | "ttm"
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


class TradingCalendarRow(BaseModel):
    """`trading_calendar` — one Taiwan market trading day. Reference table, no symbol scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    trade_date: date_


class PriceTickRow(BaseModel):
    """`price_tick` — one tick-level trade record. Backer/Sponsor-only; single trading day per
    request upstream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    time: str
    deal_price: float
    volume: int
    tick_type: str  # "0" unknown, "1" buyer-initiated, "2" seller-initiated


class ValuationRatiosRow(BaseModel):
    """`valuation_ratios` — one day's PER/PBR/dividend-yield row for one stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    dividend_yield: float
    per: float
    pbr: float


class OrderBookStatsRow(BaseModel):
    """`order_book_stats` — one 5-second whole-exchange order-book/trade aggregate. Market-level,
    no symbol scope; single trading day per request upstream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    trade_date: date_
    time: str
    total_buy_order: int
    total_buy_volume: int
    total_sell_order: int
    total_sell_volume: int
    total_deal_order: int
    total_deal_volume: int
    total_deal_money: int


class Taiex5sIndexRow(BaseModel):
    """`taiex_5s_index` — one day's TAIEX index reading. Market-level, no symbol scope; single
    trading day per request upstream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    trade_date: date_
    taiex: float


class DayTradingRow(BaseModel):
    """`day_trading` — one day-trading (當沖) eligibility/volume row for one stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    buy_after_sale: str  # "*" sell-first-then-buy suspended; "Y"/"" both directions allowed
    volume: int
    buy_amount: int
    sell_amount: int


class TotalReturnIndexRow(BaseModel):
    """`total_return_index` — one day's TAIEX/TPEx total-return index reading. `symbol` here is
    the pseudo-code `TAIEX` or `TPEx`, not a tradable stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    price: float


class TenYearPriceRow(BaseModel):
    """`ten_year_price` — one day's close on the ten-year price line. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    close: float


class KBarRow(BaseModel):
    """`intraday_kbar` — one minute-bar for one stock. Sponsor-only; single trading day per
    request upstream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    minute: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class PeriodPriceRow(BaseModel):
    """`price_weekly` / `price_monthly` — one week-K or month-K bar for one stock. Backer/Sponsor
    only; `period` carries the upstream `yweek`/`ymonth` label."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    period: str
    trade_date: date_
    open: float
    high: float
    low: float
    close: float
    trading_volume: int
    trading_money: int
    trading_turnover: float


class Index5sSnapshotRow(BaseModel):
    """`index_5s_snapshot` — one 5-second index reading. Backer/Sponsor-only; single trading day
    per request upstream. No `data_id` is ever meaningful for this dataset — the full set of
    index codes comes back regardless of what's passed, so `symbol` is read off each row's own
    `stock_id`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    time: str
    price: float
    kind: str


class TradingSuspensionRow(BaseModel):
    """`trading_suspension` — one 暫停交易 (trading-halt) announcement row. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    date: date_
    suspension_time: str
    resumption_date: str | None = None
    resumption_time: str | None = None


class DayTradingSuspensionRow(BaseModel):
    """`day_trading_suspension` — one 暫停先賣後買當沖 (sell-first day-trading suspension)
    announcement row. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    date: date_
    end_date: date_
    reason: str


class PriceLimitRow(BaseModel):
    """`price_limit` — one day's reference/limit-up/limit-down price row for one stock.
    `limit_up`/`limit_down` of 0 means no price-change limit applies (leveraged/inverse ETFs,
    emerging-board stocks)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    reference_price: float
    limit_up: float
    limit_down: float


class MarginShortRow(BaseModel):
    """`margin_short` — one day's 融資融劵 (margin purchase / short sale) balance row for one
    stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    margin_purchase_buy: int
    margin_purchase_cash_repayment: int
    margin_purchase_limit: int
    margin_purchase_sell: int
    margin_purchase_today_balance: int
    margin_purchase_yesterday_balance: int
    note: str | None = None
    offset_loan_and_short: int
    short_sale_buy: int
    short_sale_cash_repayment: int
    short_sale_limit: int
    short_sale_sell: int
    short_sale_today_balance: int
    short_sale_yesterday_balance: int


class MarginShortTotalMarketRow(BaseModel):
    """`margin_short_total_market` — one day's whole-market margin/short-sale total row for one
    category (`name`, e.g. TWSE/TPEx). Market-level, no symbol scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    trade_date: date_
    name: str
    buy: int
    sell: int
    today_balance: int
    yesterday_balance: int
    return_: float


class InstitutionalFlowWideRow(BaseModel):
    """`institutional_flow_wide` — same data as `institutional_flow` but reshaped one row per
    (date, symbol), each institutional-investor category its own buy/sell column. A category is
    0 in eras where it did not exist — sum `dealer` + `dealer_self` + `dealer_hedging` for a
    continuous dealer total across the 2014-12-01 category split."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    foreign_investor_buy: int
    foreign_investor_sell: int
    foreign_dealer_self_buy: int
    foreign_dealer_self_sell: int
    investment_trust_buy: int
    investment_trust_sell: int
    dealer_buy: int
    dealer_sell: int
    dealer_self_buy: int
    dealer_self_sell: int
    dealer_hedging_buy: int
    dealer_hedging_sell: int


class InstitutionalFlowTotalMarketRow(BaseModel):
    """`institutional_flow_total_market` — one day's whole-market three-major-institutional-
    investor buy/sell total row for one category (`name`). Market-level, no symbol scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    trade_date: date_
    name: str
    buy: int
    sell: int


class ForeignShareholdingRow(BaseModel):
    """`foreign_shareholding` — one day's 外資持股 (foreign shareholding) row for one stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    stock_name: str
    international_code: str
    foreign_investment_remaining_shares: int
    foreign_investment_shares: int
    foreign_investment_remain_ratio: float
    foreign_investment_shares_ratio: float
    foreign_investment_upper_limit_ratio: float
    chinese_investment_upper_limit_ratio: float
    number_of_shares_issued: int
    recently_declare_date: str | None = None
    note: str | None = None


class ShareholdingDistributionRow(BaseModel):
    """`shareholding_distribution` — one 股權持股分級表 (shareholding-level distribution) row for
    one stock. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    holding_shares_level: str
    people: int
    percent: float
    unit: int


class SecuritiesLendingRow(BaseModel):
    """`securities_lending` — one 借券成交 (securities-lending transaction) row for one stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    transaction_type: str
    volume: int
    fee_rate: float
    close: float
    original_return_date: str | None = None
    original_lending_period: str | None = None


class MarginShortSaleSuspensionRow(BaseModel):
    """`margin_short_sale_suspension` — one 暫停融券賣出 (short-sale suspension) announcement
    row for one stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    date: date_
    end_date: date_
    reason: str


class DailyShortSaleBalancesRow(BaseModel):
    """`daily_short_sale_balances` — one day's 信用額度總量管制餘額 (credit-limit short-sale
    balance) row for one stock, covering both margin (Margin*) and securities-lending (SBL*)
    short-sale quota/balance figures."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    margin_short_sales_previous_day_balance: int
    margin_short_sales_short_sales: int
    margin_short_sales_short_covering: int
    margin_short_sales_stock_redemption: int
    margin_short_sales_current_day_balance: int
    margin_short_sales_quota: int
    sbl_short_sales_previous_day_balance: int
    sbl_short_sales_short_sales: int
    sbl_short_sales_returns: int
    sbl_short_sales_adjustments: int
    sbl_short_sales_current_day_balance: int
    sbl_short_sales_quota: int
    sbl_short_sales_short_covering: int


class SecuritiesTraderInfoRow(BaseModel):
    """`securities_trader_info` — one 證券商 (securities-trader/broker) directory row.
    Market-level reference table, no symbol scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    trader_id: str
    trader_name: str
    as_of: date_
    address: str | None = None
    phone: str | None = None


class GovernmentBankBuySellRow(BaseModel):
    """`government_bank_buysell` — one day's 八大行庫 (eight state-owned/government banks)
    buy/sell row for one stock. Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    buy_amount: float
    sell_amount: float
    buy: int
    sell: int
    bank_name: str


class MarginMaintenanceRatioRow(BaseModel):
    """`margin_maintenance_ratio` — one day's whole-market 融資維持率 (margin maintenance ratio)
    reading. Backer/Sponsor-only; market-level, no symbol scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    trade_date: date_
    total_exchange_margin_maintenance: float


class ChipBrokerAggRow(BaseModel):
    """`chip_broker_agg` — one day's per-broker aggregated 分點 (branch) buy/sell volume/price
    row for one stock. Sponsor-only; served by FinMind's dedicated aggregate endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    broker_id: str
    broker_name: str
    buy_volume: int
    sell_volume: int
    buy_price: float
    sell_price: float


class BlockTradeBrokerReportRow(BaseModel):
    """`block_trade_broker_report` — one day's 鉅額交易 (block-trade) broker-level buy/sell row
    for one stock. Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    broker_id: str
    broker_name: str
    price: float
    buy: int
    sell: int
    trade_type: str


class BlockTradeRow(BaseModel):
    """`block_trade` — one day's 鉅額交易 (block-trade) execution row for one stock.
    Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    trade_type: str
    price: float
    volume: int
    trading_money: float


class LoanCollateralBalanceRow(BaseModel):
    """`loan_collateral_balance` — one day's 借貸款項擔保品餘額 (loan-collateral balance) row for
    one stock, covering margin, securities-firm-loan, unrestricted-loan, securities-finance-
    secured-loan, and settlement-margin balances. Sponsor-only. `exchange_market` is the upstream
    `market` (TWSE/TPEx) text column — kept distinct from the `Market` enum field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    exchange_market: str
    margin_previous_day_balance: int
    margin_buy: int
    margin_sell: int
    margin_cash_redemption: int
    margin_current_day_balance: int
    margin_next_day_quota: int
    securities_firm_loan_previous_day_balance: int
    securities_firm_loan_buy: int
    securities_firm_loan_sell: int
    securities_firm_loan_cash_redemption: int
    securities_firm_loan_replacement: int
    securities_firm_loan_current_day_balance: int
    securities_firm_loan_next_day_quota: int
    unrestricted_loan_previous_day_balance: int
    unrestricted_loan_buy: int
    unrestricted_loan_sell: int
    unrestricted_loan_cash_redemption: int
    unrestricted_loan_replacement: int
    unrestricted_loan_current_day_balance: int
    unrestricted_loan_next_day_quota: int
    securities_finance_secured_loan_previous_day_balance: int
    securities_finance_secured_loan_buy: int
    securities_finance_secured_loan_sell: int
    securities_finance_secured_loan_cash_redemption: int
    securities_finance_secured_loan_replacement: int
    securities_finance_secured_loan_current_day_balance: int
    securities_finance_secured_loan_next_day_quota: int
    settlement_margin_previous_day_balance: int
    settlement_margin_buy: int
    settlement_margin_sell: int
    settlement_margin_cash_redemption: int
    settlement_margin_replacement: int
    settlement_margin_current_day_balance: int
    settlement_margin_next_day_quota: int


class IndustryChainMoneyFlowRow(BaseModel):
    """`industry_chain_money_flow` — one day's 產業鏈資金流向 (industry-chain money-flow)
    aggregate row for one industry (or sub-industry). Sponsor-only; market-level, no symbol
    scope. `sub_industry` empty means an industry-chain total row — these do NOT equal the sum
    of that chain's sub-industry rows (a stock can belong to multiple sub-industries), and a
    stock can belong to multiple chains, so `trading_money_pct` across chains sums to more than
    100%.

    `locked=True` rows (governance.md §4 top-N-preview exception) have every field but `rank`/
    `market`/`trade_date`/`is_total` nulled server-side — `sub_industry` itself is nulled, so
    `is_total` (not `sub_industry == ""`) is how a caller/renderer tells a locked chain-total
    row from a locked sub-industry breakdown row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int = 0  # 1-based position within its own group (total vs. breakdown) — real if locked
    market: Market
    trade_date: date_
    is_total: bool = False  # mirrors `sub_industry == ""` at construction; survives masking
    industry: str | None
    sub_industry: str | None
    stock_count: int | None
    trading_volume: int | None
    trading_money: float | None
    trading_money_pct: float | None
    locked: bool = False


class DispositionSecuritiesRow(BaseModel):
    """`disposition_securities` — one 公布處置有價證券 (disposition-securities announcement) row
    for one stock. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    date: date_
    stock_name: str
    disposition_cnt: int
    condition: str
    measure: str
    period_start: date_
    period_end: date_


class AttentionStockRow(BaseModel):
    """`attention_stocks` — one 注意股 (attention-stock) flag for one stock, whole-market,
    sourced from TWSE's `announcement/notice` and TPEX's `tpex_trading_warning_information`
    OpenAPI feeds (`sources/twse_openapi.py`/`sources/tpex_openapi.py`). `security_type`
    distinguishes exchange (`"twse"`/`"tpex"`) — both model as `Market.TW`, which has no
    exchange split (`schemas/enums.py`). Forward-only: no date parameter upstream, so this is
    the current day's flags only — accumulating daily partitions over time is what backs the
    處置王 board's rolling-window attention-flag count, not a queryable history on its own."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    company_name: str
    flagged_date: date_
    reason: str
    close: float | None = None
    pe: float | None = None
    security_type: str  # "twse" / "tpex"


class DispositionEventRow(BaseModel):
    """`disposition_events` — one 處置 (disposition) announcement for one stock, whole-market,
    sourced from TWSE's `announcement/punish` and TPEX's `tpex_disposal_information` OpenAPI
    feeds. Distinct from `DispositionSecuritiesRow` above (FinMind's per-symbol
    `TaiwanStockDispositionSecuritiesPeriod`) — this is the whole-market feed the 處置王 board's
    bucket classification reads back from the derived store, not a per-symbol live fetch.
    `security_type` distinguishes exchange (`"twse"`/`"tpex"`); `reason`/`measure` carry each
    exchange's own free-text field (TWSE: `ReasonsOfDisposition`/`DispositionMeasures`; TPEX:
    `DispositionReasons`/`DisposalCondition` — TPEX has no measures/reasons split, so `measure`
    holds `DisposalCondition`'s full text for that exchange). Forward-only, same as
    `AttentionStockRow`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    company_name: str
    announced_date: date_
    reason: str
    period_start: date_
    period_end: date_
    measure: str
    detail: str | None = None
    security_type: str  # "twse" / "tpex"


class SelfAssessedEarningsRow(BaseModel):
    """`self_assessed_earnings` — one 自結損益公告 (self-assessed/preliminary P&L) row for one
    stock, from MOPS's `t138sb01` monthly-summary report (`sources/mops.py`). Unlike
    `AttentionStockRow`/`DispositionEventRow`, this is fetched bounded per-symbol (the upstream
    form has no whole-market date-range query — only one market/period combination per POST,
    still whole-market for THAT period, but with no way to ask "every period a company has ever
    filed" in one call) — see `fetch_self_assessed_earnings`'s own docstring. `consolidated`
    distinguishes an individual-statement row (`個別`) from a consolidated one (`合併`); a company
    filing both submits two rows for the same period. `revenue_period` is the ISO "YYYY-MM" (for
    a monthly filing) the filing covers — NOT the filing/announcement date, which this report
    does not carry per-row (MOPS's own summary table has no per-row announcement timestamp)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    company_name: str
    revenue_period: str  # ISO "YYYY-MM"
    consolidated: bool
    monthly_operating_profit: int | None = None  # 仟元 (thousand-NTD), upstream unit
    ytd_operating_profit: int | None = None
    monthly_pretax_profit: int | None = None
    ytd_pretax_profit: int | None = None
    variance_note: str | None = None  # upstream 差異原因說明


class DispositionBoardCardRow(BaseModel):
    """`disposition_board` — one 處置王-style card, `GetDispositionBoardTool`'s output row.
    Joins `AttentionStockRow`/`DispositionEventRow` (bucket classification + projection) against
    `PRICE_EOD_JOB` closes and `INSTITUTIONAL_FLOW_JOB` flow (both already-ingested, whole-
    market) plus a bounded per-symbol `get_margin_short` fan-out (never a live whole-market
    scan). `projection_caveat` is always populated — states plainly that
    `days_until_next_disposition` is this codebase's own rule-based encoding of TWSE's publicly
    stated escalation pattern, not a verbatim TWSE-published number, and is bounded by however
    much attention-flag history has accumulated since this feature's ingest jobs first ran.
    Three buckets (風險股/處置中/即將出關); 自結發布 is not a 4th bucket — it's a per-card
    enrichment (`self_assessed_*` fields below), since a company's preliminary-earnings filing
    is orthogonal to its attention/disposition status, not a bucket of its own."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int = 0  # 1-based position within this card's bucket — real even when locked
    symbol: str | None
    market: Market
    company_name: str | None
    security_type: str | None  # "twse" (上市) / "tpex" (上櫃)
    close: float | None = None
    change_pct: float | None = None
    volume: int | None = None
    turnover: float | None = None
    institutional_net: int | None = None
    margin_ratio: float | None = None
    # `margin_ratio` comes from a live/cached per-symbol `get_margin_short`-equivalent fetch
    # (never a whole-market ingest), so its own `SourceResult.currentness_caveat`/`health_status`
    # must travel with it here, not be discarded at fetch time — this is the only field on this
    # row not read straight back from an ingest-owned Parquet partition.
    margin_caveat: str | None = None
    margin_health_status: str | None = None
    bucket: str  # "at_risk" / "in_disposition" / "exiting_soon"
    disposition_measure: str | None = None
    disposition_period_start: date_ | None = None
    disposition_period_end: date_ | None = None
    # The real-world 撮合終端機 cadence TWSE/TPEx's own free-text `detail`/`measure` states for
    # this disposition (e.g. "約每2分鐘撮合一次") — minutes, not a fixed 5/20 enum; TWSE's
    # published examples cluster around ~2min and ~45min but the actual number is per-announcement
    # free text, so this is parsed, not a closed set.
    matching_interval_minutes: int | None = None
    flags: tuple[str, ...] = Field(default_factory=tuple)
    # A COUNT, not a day figure despite how it reads — how many more 注意股 flags (occurrences,
    # not calendar/trading days) this at_risk symbol needs before this codebase's own rule
    # treats it as escalating to disposition. Named `_remaining` (not `days_until_...`) so the
    # field itself doesn't lie about its own unit — see `projection_caveat`.
    attention_flags_remaining: int | None = None
    # A REAL trading-day estimate: `attention_flags_remaining` extrapolated against this
    # symbol's own observed flag rate over the rolling window (`flags_so_far / observed_days`),
    # rounded up. `None` whenever the rate is zero/undefined (no flags observed yet — can't
    # divide by zero, and a rate-less symbol has no basis for a day estimate) or the symbol is
    # already disposed. This is an estimate layered on top of `attention_flags_remaining`'s own
    # already-approximate rule — see `projection_caveat`, which states both explicitly.
    estimated_days_until_disposition: int | None = None
    # Distinct from `attention_flags_remaining` (which projects a NOT-yet-disposed symbol's
    # escalation) — this IS a real day count: `period_end - today` for a symbol CURRENTLY
    # disposed (in_disposition/exiting_soon), i.e. "days remaining in the active disposition
    # period", `None` for at_risk.
    days_until_exiting: int | None = None
    # Day-trading (當沖) volume ÷ total volume for the report date, from a bounded per-symbol
    # `get_day_trading`-equivalent fetch (same fan-out shape as margin — never a whole-market
    # scan). `None` when no day-trading row exists for this symbol/date.
    day_trading_ratio: float | None = None
    # 自結發布 (自結損益) — a bounded per-symbol `get_self_assessed_earnings`-equivalent fetch,
    # same fan-out shape as margin/day-trading (never a whole-market scan; the upstream form has
    # no whole-history query anyway — see `SelfAssessedEarningsRow`'s own docstring). `None`
    # whenever this symbol has no filing for the checked period (most stocks, most months — this
    # is a voluntary disclosure). `self_assessed_period`/`self_assessed_consolidated` state
    # exactly which filing (period, individual-vs-consolidated) the profit figures below came
    # from, since a company may file both for the same month.
    self_assessed_period: str | None = None
    self_assessed_consolidated: bool | None = None
    self_assessed_monthly_pretax_profit: int | None = None
    self_assessed_ytd_pretax_profit: int | None = None
    projection_caveat: str = Field(min_length=1)
    # governance.md §4 top-N-preview exception: `True` only for a card beyond
    # `GetDispositionBoardOutput.free_row_count` within its own bucket, shown to an unentitled
    # caller with every field but `rank`/`bucket`/`market`/`projection_caveat` nulled server-side
    # (`flags` becomes empty) — `bucket` survives since it's the tab filter itself (categorical,
    # not identifying), and `projection_caveat` is static copy, not this card's own data.
    locked: bool = False


class DividendResultRow(BaseModel):
    """`dividend_result` — one 除權除息結果 (ex-dividend/ex-rights result) row for one stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    before_price: float
    after_price: float
    stock_and_cache_dividend: float
    stock_or_cache_dividend: str
    max_price: float
    min_price: float
    open_price: float
    reference_price: float


class MonthRevenueRow(BaseModel):
    """`month_revenue` — one 月營收 (monthly revenue) filing row for one stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    country: str
    revenue: int
    revenue_month: int
    revenue_year: int
    create_time: str | None = None


class CapitalReductionReferencePriceRow(BaseModel):
    """`capital_reduction_reference_price` — one 減資恢復買賣參考價格 (post-capital-reduction
    reference price) row for one stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    closing_price_on_last_trading_day: float
    post_reduction_reference_price: float
    limit_up: float
    limit_down: float
    opening_reference_price: float
    exright_reference_price: float
    reason_for_capital_reduction: str


class MarketValueRow(BaseModel):
    """`market_value` — one day's market-capitalization row for one stock. Backer/Sponsor-only,
    no free per-symbol tier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    market_value: int


class StockDelistingRow(BaseModel):
    """`stock_delisting` — one 下市櫃 (delisting) event row for one stock. Reference table, no
    `data_id` param exists."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    date: date_
    stock_name: str


class MarketValueWeightRow(BaseModel):
    """`market_value_weight` — one day's whole-market市值比重 (market-cap-weight) ranking row for
    one stock. Backer/Sponsor-only, no free per-symbol tier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    rank: int
    stock_name: str
    weight_per: float
    listing_type: str  # upstream "type": twse / tpex


class SplitPriceRow(BaseModel):
    """`split_price` — one stock split / reverse split reference row for one stock.

    FinMind's TW dataset (股票分割/反分割) always populates the reference-price fields; yfinance's
    non-TW `.splits` feed carries only the split ratio (no reference prices at all), so those
    fields are optional here rather than fabricated for the yfinance case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    split_type: str  # FinMind upstream "type" (分割/反分割) or a yfinance ratio label
    before_price: float | None = None
    after_price: float | None = None
    max_price: float | None = None
    min_price: float | None = None
    open_price: float | None = None
    split_ratio: float | None = None  # yfinance-only: e.g. 4.0 for a 4-for-1 split


class ParValueChangeRow(BaseModel):
    """`par_value_change` — one 變更面額恢復買賣參考價格 (par-value-change reference price) row
    for one stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    date: date_
    stock_name: str
    before_close: float
    after_ref_close: float
    after_ref_max: float
    after_ref_min: float
    after_ref_open: float


class FutOptInfoRow(BaseModel):
    """`futopt_info` — one row of the whole futures/options contract-code roster reference
    table. Free tier; no `data_id` param exists."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    code: str
    contract_type: str  # upstream "type"
    name: str


class FuturesDailyRow(BaseModel):
    """`futures_daily` — one day's futures contract EOD price/volume row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # futures_id
    market: Market
    trade_date: date_
    contract_date: str
    open: float
    high: float
    low: float
    close: float
    spread: float
    spread_per: float
    volume: int
    settlement_price: float
    open_interest: int
    trading_session: str


class OptionDailyRow(BaseModel):
    """`option_daily` — one day's option contract EOD price/volume row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # option_id
    market: Market
    trade_date: date_
    contract_date: str
    strike_price: float
    call_put: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    settlement_price: float
    open_interest: int
    trading_session: str


class FuturesTickRow(BaseModel):
    """`futures_tick` — one futures per-trade tick. Backer/Sponsor-only; single trading day per
    request upstream. Tick volume is double-sided (each matched trade records both buy and sell
    once, so summed tick volume is ~2x `futures_daily`'s single-side volume; ~4x for spread/
    combination orders) — passed through as-is, never halved here. The after-hours (night)
    session is attributed to the *next* business day by TAIFEX convention: 00:00-05:00 ticks
    belong to day D's after-market, 08:45-13:45 to day D's regular session, 15:00-24:00 to the
    next trading day's after-market — this mapper does not re-bucket `date`, it passes the
    upstream trading-day attribution through unchanged."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # futures_id
    market: Market
    trade_date: date_
    contract_date: str
    price: float
    volume: int


class FuturesSpreadTickRow(BaseModel):
    """`futures_spread_tick` — one futures calendar-spread (near/far month) per-trade tick.
    Sponsor-only; single trading day per request upstream. `contract_date` carries both legs
    (e.g. `202606/202607`); `price` is the spread price and can be negative;
    `spread_to_spread` is 1 for a spread-to-spread deal, else 0."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # futures_id (spread product code)
    market: Market
    trade_date: date_
    time: str
    contract_date: str
    price: float
    volume: int
    near_price: float
    far_price: float
    spread_to_spread: int


class OptionTickRow(BaseModel):
    """`option_tick` — one option per-trade tick. Backer/Sponsor-only; single trading day per
    request upstream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # option_id
    market: Market
    trade_date: date_
    contract_date: str
    exercise_price: float
    put_call: str
    price: float
    volume: int


class FuturesInstitutionalFlowRow(BaseModel):
    """`futures_institutional_flow` — one day's three-major-institutional-investor futures
    buy/sell + open-interest-balance row. The upstream columns for this dataset do not include a
    per-contract identifier, so on a whole-market (omit-`data_id`) call `symbol` reads empty —
    a documented upstream gap, not a mapper defect."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # futures_id when known from the per-symbol request; "" on whole-market calls
    market: Market
    trade_date: date_
    investor: str  # upstream "institutional_investors"
    long_deal_volume: int
    long_deal_amount: int
    short_deal_volume: int
    short_deal_amount: int
    long_open_interest_balance_volume: int
    long_open_interest_balance_amount: int
    short_open_interest_balance_volume: int
    short_open_interest_balance_amount: int


class OptionInstitutionalFlowRow(BaseModel):
    """`option_institutional_flow` — one day's three-major-institutional-investor option
    buy/sell + open-interest-balance row, split by `call_put`. The upstream columns for this
    dataset do not include a per-contract identifier, so on a whole-market (omit-`data_id`) call
    `symbol` reads empty — a documented upstream gap, not a mapper defect."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # option_id when known from the per-symbol request; "" on whole-market calls
    market: Market
    trade_date: date_
    call_put: str
    investor: str
    long_deal_volume: int
    long_deal_amount: int
    short_deal_volume: int
    short_deal_amount: int
    long_open_interest_balance_volume: int
    long_open_interest_balance_amount: int
    short_open_interest_balance_volume: int
    short_open_interest_balance_amount: int


class FuturesInstitutionalFlowAfterHoursRow(BaseModel):
    """`futures_institutional_flow_after_hours` — one night-session three-major-institutional-
    investor futures buy/sell row. Sponsor-only; unlike the day-session dataset there is no
    open-interest-balance breakdown here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # futures_id
    market: Market
    trade_date: date_
    investor: str
    long_deal_volume: int
    long_deal_amount: int
    short_deal_volume: int
    short_deal_amount: int


class OptionInstitutionalFlowAfterHoursRow(BaseModel):
    """`option_institutional_flow_after_hours` — one night-session three-major-institutional-
    investor option buy/sell row, split by `call_put`. Sponsor-only; unlike the day-session
    dataset there is no open-interest-balance breakdown here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # option_id
    market: Market
    trade_date: date_
    call_put: str
    investor: str
    long_deal_volume: int
    long_deal_amount: int
    short_deal_volume: int
    short_deal_amount: int


class FuturesDealerVolumeRow(BaseModel):
    """`futures_dealer_volume` — one day's per-dealer futures trading-volume row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # futures_id
    market: Market
    trade_date: date_
    dealer_code: str
    dealer_name: str
    volume: int
    is_after_hour: bool


class OptionDealerVolumeRow(BaseModel):
    """`option_dealer_volume` — one day's per-dealer option trading-volume row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # option_id
    market: Market
    trade_date: date_
    dealer_code: str
    dealer_name: str
    volume: int
    is_after_hour: bool


class FuturesOiLargeTradersRow(BaseModel):
    """`futures_oi_large_traders` — one day's 大額交易人未沖銷部位 (large-trader open-interest
    concentration) row for one futures contract type. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # futures_id
    market: Market
    trade_date: date_
    name: str
    contract_type: str
    buy_top5_trader_open_interest: int
    buy_top5_trader_open_interest_per: float
    buy_top10_trader_open_interest: int
    buy_top10_trader_open_interest_per: float
    sell_top5_trader_open_interest: int
    sell_top5_trader_open_interest_per: float
    sell_top10_trader_open_interest: int
    sell_top10_trader_open_interest_per: float
    market_open_interest: int
    buy_top5_specific_open_interest: int
    buy_top5_specific_open_interest_per: float
    buy_top10_specific_open_interest: int
    buy_top10_specific_open_interest_per: float
    sell_top5_specific_open_interest: int
    sell_top5_specific_open_interest_per: float
    sell_top10_specific_open_interest: int
    sell_top10_specific_open_interest_per: float


class OptionOiLargeTradersRow(BaseModel):
    """`option_oi_large_traders` — one day's 大額交易人未沖銷部位 (large-trader open-interest
    concentration) row for one option contract type, split by `put_call`. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # option_id
    market: Market
    trade_date: date_
    put_call: str
    name: str
    contract_type: str
    buy_top5_trader_open_interest: int
    buy_top5_trader_open_interest_per: float
    buy_top10_trader_open_interest: int
    buy_top10_trader_open_interest_per: float
    sell_top5_trader_open_interest: int
    sell_top5_trader_open_interest_per: float
    sell_top10_trader_open_interest: int
    sell_top10_trader_open_interest_per: float
    market_open_interest: int
    buy_top5_specific_open_interest: int
    buy_top5_specific_open_interest_per: float
    buy_top10_specific_open_interest: int
    buy_top10_specific_open_interest_per: float
    sell_top5_specific_open_interest: int
    sell_top5_specific_open_interest_per: float
    sell_top10_specific_open_interest: int
    sell_top10_specific_open_interest_per: float


class FuturesSpreadTradingRow(BaseModel):
    """`futures_spread_trading` — one day's 期貨價差 (futures calendar-spread) quote row.
    Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # futures_id
    market: Market
    trade_date: date_
    contract_date: str
    open: float
    high: float
    low: float
    close: float
    best_bid: float
    best_ask: float
    historical_max: float
    historical_min: float
    spread_to_spread_volume: int
    spread_to_single_volume: int
    trading_session: str


class FinalSettlementPriceRow(BaseModel):
    """`futures_final_settlement_price` / `option_final_settlement_price` — one contract's
    最後結算價 (final settlement price) row. Backer/Sponsor-only; shared shape for both the
    futures and option variants (upstream `futures_type`/`option_type` and
    `futures_name`/`option_name` map to `instrument_type`/`instrument_name` here)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # futures_id / option_id
    market: Market
    trade_date: date_
    contract_month: str
    instrument_type: str
    instrument_name: str
    settlement_price: float
    underlying_code: str
    notional_value: float


class OptionVixRow(BaseModel):
    """`option_vix` — one 臺指選擇權波動率指數 (TAIEX option volatility index) reading.
    Backer/Sponsor-only; market-level, no symbol scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    trade_date: date_
    time: str
    vix: float


class AssetSwapFixedIncomeDailyRow(BaseModel):
    """`asset_swap_fixed_income_daily` — one day's 資產交換固定收益 (fixed-income asset-swap)
    trading row for one stock. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    stock_name: str
    notional_amount: int
    number_of_transactions: int
    rate_lowest: float
    rate_highest: float
    rate_average: float
    contract_term_years: float


class AssetSwapOptionDailyRow(BaseModel):
    """`asset_swap_option_daily` — one day's 資產交換選擇權 (option asset-swap) trading row for
    one stock. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    stock_name: str
    notional_amount: int
    number_of_transactions: int
    premium_lowest: float
    premium_highest: float
    premium_average: float
    contract_term_years: float


class TickSnapshotRow(BaseModel):
    """`tick_snapshot` — one bounded intraday tick-level snapshot. Sponsor-only. `symbol` holds
    either a 4-digit stock id or a 3-digit index code (91 documented index codes — broad-market/
    sector/themed/leverage — besides ordinary stocks); this is a data_id-taxonomy quirk, not a
    stock-only restriction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    amount: float
    average_price: float
    buy_price: float
    buy_volume: int
    change_price: float
    change_rate: float
    close: float
    high: float
    low: float
    open: float
    sell_price: float
    sell_volume: int
    total_amount: float
    total_volume: int
    volume: int
    volume_ratio: float
    yesterday_volume: int
    tick_type: str


class FutOptTickInfoRow(BaseModel):
    """`futopt_tick_info` — one row of the whole futures/options real-time-quote contract
    reference roster. Free tier; static list, not a per-date time series."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    code: str
    call_put: str
    as_of: date_
    name: str
    listing_date: date_ | None = None
    expire_price: float | None = None
    update_date: str | None = None


class FuturesSnapshotRow(BaseModel):
    """`futures_snapshot` — one bounded intraday futures-contract snapshot. Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # futures_id
    market: Market
    trade_date: date_
    open: float
    high: float
    low: float
    close: float
    change_price: float
    change_rate: float
    average_price: float
    volume: int
    total_volume: int
    amount: float
    total_amount: float
    yesterday_volume: int
    buy_price: float
    buy_volume: int
    sell_price: float
    sell_volume: int
    volume_ratio: float
    tick_type: str


class OptionsSnapshotRow(BaseModel):
    """`options_snapshot` — one bounded intraday options-contract snapshot. Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # options_id
    market: Market
    trade_date: date_
    open: float
    high: float
    low: float
    close: float
    change_price: float
    change_rate: float
    average_price: float
    volume: int
    total_volume: int
    amount: float
    total_amount: float
    yesterday_volume: int
    buy_price: float
    buy_volume: int
    sell_price: float
    sell_volume: int
    volume_ratio: float
    tick_type: str


class ConvertibleBondInfoRow(BaseModel):
    """`cb_info` — one 可轉債 (convertible bond) roster row. `symbol` is the bond's `cb_id` —
    a convertible-bond identifier, distinct from an equity `stock_id`, never conflated
    downstream. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # cb_id
    market: Market
    name: str  # cb_name
    initial_date_of_conversion: date_ | None = None
    due_date_of_conversion: date_ | None = None
    issuance_amount: float | None = None


class ConvertibleBondDailyRow(BaseModel):
    """`cb_daily` — one convertible bond's EOD trading row. `symbol` is the `cb_id`, not an
    equity `stock_id`. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # cb_id
    market: Market
    name: str  # cb_name
    trade_date: date_
    transaction_type: str
    close: float
    change: float
    open: float
    high: float
    low: float
    number_of_transactions: int
    unit: str
    trading_value: float
    avg_price: float
    next_ref_price: float
    next_max_limit: float
    next_min_limit: float


class ConvertibleBondInstitutionalFlowRow(BaseModel):
    """`cb_institutional_flow` — one convertible bond's three-major-institutional-investor
    daily buy/sell/overbuy row. `symbol` is the `cb_id`. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # cb_id
    market: Market
    name: str  # cb_name
    trade_date: date_
    foreign_investor_buy: float
    foreign_investor_sell: float
    foreign_investor_overbuy: float
    investment_trust_buy: float
    investment_trust_sell: float
    investment_trust_overbuy: float
    dealer_self_buy: float
    dealer_self_sell: float
    dealer_self_overbuy: float
    total_overbuy: float


class ConvertibleBondDailyOverviewRow(BaseModel):
    """`cb_daily_overview` — one convertible bond's daily overview row, including conversion/
    put/early-redemption terms. `symbol` is the `cb_id`. Backer/Sponsor-only. The put/early-
    redemption fields (`latest_initial_date_of_put` through `early_redemption_price`) are
    populated only while a put or early-redemption process is publicly announced for that
    bond — empty on most bonds most days is expected, not missing data; this dataset does not
    carry the bond's full static issuance terms (e.g. a complete put schedule)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # cb_id
    market: Market
    name: str  # cb_name
    trade_date: date_
    initial_date_of_conversion: date_ | None = None
    due_date_of_conversion: date_ | None = None
    initial_date_of_stop_conversion: date_ | None = None
    due_date_of_stop_conversion: date_ | None = None
    conversion_price: float | None = None
    next_effective_date_of_conversion_price: date_ | None = None
    latest_initial_date_of_put: date_ | None = None
    latest_due_date_of_put: date_ | None = None
    latest_put_price: float | None = None
    initial_date_of_early_redemption: date_ | None = None
    due_date_of_early_redemption: date_ | None = None
    early_redemption_price: float | None = None
    date_of_delisted: date_ | None = None
    issuance_amount: float | None = None
    outstanding_amount: float | None = None
    reference_price: float | None = None
    price_of_underlying_stock: float | None = None
    initial_date_of_suspension: date_ | None = None
    due_date_of_suspension: date_ | None = None
    coupon_rate: float | None = None


class ConvertibleBondMonthlyAnalysisRow(BaseModel):
    """`cb_monthly_analysis` — one convertible bond's monthly custody-balance analysis row.
    `symbol` is the `cb_id`. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # cb_id
    market: Market
    name: str  # cb_name
    name_en: str
    trade_date: date_
    custody_balance: int
    last_month_balance: int
    change: int
    change_percent: float
    issued_units: int
    custody_accounts: int
    pledged_units: int


class ConvertibleBondPutProvisionRow(BaseModel):
    """`cb_put_provision` — one convertible bond's 賣回權 (put) schedule row (`date` is the put
    record date; announced future put dates typically extend ~1 year ahead). `symbol` is the
    `cb_id`. Backer/Sponsor-only; complements `cb_daily_overview`'s put fields, which are only
    populated during announced put windows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # cb_id
    market: Market
    date: date_
    name: str  # cb_name
    put_price: float
    put_yield_rate: float


class StockNewsRow(BaseModel):
    """`stock_news` — one FinMind-sourced news item for one stock. Third-party content: cite,
    never restate as our own analysis. Distinct from the `news` capability's RSS/Finnhub
    aggregator (`sources/news.py`) — this is FinMind's own dataset, wired the same as every
    other FinMind capability. Free tier; single trading day per request upstream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    description: str
    link: str
    source: str
    title: str


class BusinessIndicatorRow(BaseModel):
    """`business_indicator` — one month's 景氣對策信號 (business/economic-climate indicator)
    reading. Backer/Sponsor-only; market-level, no symbol scope; released on the statutory
    monthly cadence, not a daily-clock-time snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    date: date_
    leading: float
    leading_notrend: float
    coincident: float
    coincident_notrend: float
    lagging: float
    lagging_notrend: float
    monitoring: float
    monitoring_color: str


class IndustryChainClassificationRow(BaseModel):
    """`industry_chain_classification` — one stock's static 產業鏈 (industry-chain) classification
    roster row. Backer/Sponsor-only. Distinct from `industry_chain_money_flow` (the derived
    daily money-flow aggregate) — this is the underlying stock-to-industry-chain mapping itself."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    industry: str
    sub_industry: str
    as_of: date_


class InternationalStockInfoRow(BaseModel):
    """`us_stock_info` / `uk_stock_info` / `europe_stock_info` / `japan_stock_info` — one roster
    row of a non-Taiwan market's stock universe reference table. Free tier. `region_detail`
    carries whatever region/exchange/sector column that market's dataset documents (US: Country;
    UK: Country; Europe: Market; Japan: Exchange) — kept as one free-text field rather than four
    diverging optional columns, since none of the four datasets share a common taxonomy for it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    name: str
    region_detail: str | None = None
    sector: str | None = None  # populated for JapanStockInfo (Sector); else None


class InternationalStockPriceRow(BaseModel):
    """`us_stock_price` / `uk_stock_price` / `europe_stock_price` / `japan_stock_price` — one
    day's EOD bar for a non-Taiwan market stock. Free tier. `data_id` for these datasets is the
    market's own quote symbol (e.g. `^DJI`, `BP.L`, `AALB.AS`, `7203.T`), never a Taiwan-style
    numeric `stock_id` — passed straight through as `symbol`, not reparsed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: int


class UsStockPriceMinuteRow(BaseModel):
    """`us_stock_price_minute` — one minute-bar for a US market stock. Backer/Sponsor-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    trade_date: date_
    close: float
    high: float
    low: float
    open: float
    volume: int


class ExchangeRateRow(BaseModel):
    """`exchange_rate` — one day's TWD-vs-foreign-currency reference rate row. Free tier;
    `symbol` is the foreign-currency code (e.g. USD/EUR/JPY), TWD is implicit as the other leg.
    Modeled `Market.TW`-scoped: this is a TWD-context dataset (the counter-currency is always
    TWD), not a generic world-FX table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # foreign-currency code
    market: Market
    trade_date: date_
    cash_buy: float
    cash_sell: float
    spot_buy: float
    spot_sell: float


class CentralBankRateRow(BaseModel):
    """`central_bank_rate` — one central bank's policy-rate reading (`symbol` is the bank code,
    e.g. FED/BOE/ECB/BOJ). Free tier; event-driven (announced at policy-meeting dates, not a
    daily-clock-time series). Modeled `Market.GLOBAL` — this is inherently a multi-country panel
    with no single-country home; forcing any one country's `Market` value onto a dozen distinct
    central banks would misrepresent the data's actual scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # central-bank code, e.g. FED/BOE/ECB/BOJ
    market: Market
    date: date_
    country: str
    full_country_name: str
    interest_rate: float


class GoldPriceRow(BaseModel):
    """`gold_price` — one day's gold spot-price reading. Free tier; market-level, no symbol
    scope. Modeled `Market.GLOBAL` — a world commodity benchmark, no single-country home."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    date: date_
    price: float


class CrudeOilPricesRow(BaseModel):
    """`crude_oil_prices` — one day's WTI/Brent crude spot-price reading. Free tier; `symbol` is
    the benchmark name (WTI/Brent). Modeled `Market.GLOBAL` — a world commodity benchmark, no
    single-country home. This is a **spot price, not a real-time quote** — the latest available
    date is typically several trading days (~1 week) behind, unlike futures real-time quotes;
    always read the actually-returned latest `date`, don't assume same-day freshness."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # WTI / Brent
    market: Market
    date: date_
    price: float


class MarketIndexStripItemRow(BaseModel):
    """`market_index_strip` — one item in the TW-tab ticker strip
    (加權指數/櫃買指數/美元/黃金/原油). Free tier only — every underlying dataset this row draws
    from (`TAIEX_5S_INDEX`/`TOTAL_RETURN_INDEX`/`EXCHANGE_RATE`/`GOLD_PRICE`/`CRUDE_OIL_PRICES`)
    is already `DataClass.PUBLIC` at its own dataset declaration; this row never represents a
    Sponsor-tier reading (those render through their own existing tools — `get_tick_snapshot`/
    `get_futures_snapshot`/`get_taiex_vix` — masked like any other Sponsor card, not folded into
    this bundle). `change`/`change_pct` are `None` when only one day's reading is available (no
    prior day to diff against), never a fabricated zero."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str  # stable id: taiex / otc / usd_twd / gold / crude_oil_wti
    label: str
    value: float
    change: float | None = None
    change_pct: float | None = None
    as_of: date_


class GovernmentBondYieldRow(BaseModel):
    """`government_bond_yield` — one day's US Treasury yield reading for one maturity tenor
    (`symbol` e.g. "United States 10-Year"). Free tier. Modeled `Market.US` — unlike
    `central_bank_rate`, this dataset's underlying is specifically the US Treasury curve, a
    genuine single-country home."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # e.g. "United States 10-Year"
    market: Market
    date: date_
    value: float


class FearGreedIndexRow(BaseModel):
    """`fear_greed_index` — one day's CNN Fear & Greed Index reading (US equity-market
    sentiment gauge). Backer/Sponsor-only; market-level, no symbol scope. Modeled `Market.US` —
    the index is specifically a US-market sentiment gauge, a genuine single-country home."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    date: date_
    fear_greed: float
    fear_greed_emotion: str


class EtfFundInfo(BaseModel):
    """`etf_holdings` (fund-level slice) — issuer/units-outstanding/tracked-index reference
    data for a listed ETF. Does **not** carry per-constituent weights (issuer PCF holdings
    remain an unfilled gap) — this is fund-level metadata only, refreshed on the issuer's
    reporting cadence, not a live figure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    fund_name: str
    fund_type: str  # 基金類型 raw string, e.g. "國內成分證券主動式交易所交易基金(股票)"
    tracked_index: str | None = None
    units_outstanding: int | None = None
    listed_date: date_ | None = None  # 上市日期 (exchange listing)
    inception_date: date_ | None = None  # 成立日期 (fund establishment) — distinct from listed_date
    manager_name: str | None = None  # 基金經理人 (the manager person)
    manager_company: str | None = None  # 投信公司 — None unless a clean company field is sourced
    custodian_bank: str | None = None  # 保管機構


class EtfThemeTags(BaseModel):
    """`etf_theme_tags` — TWSE's own "ETF e添富" (ETFortune) 主題/因子 (theme/factor)
    classification for one listed ETF (e.g. 高股息/科技主題型/公司治理及ESG) — a curated editorial
    tag set distinct from this codebase's AI-researched stock-concept themes
    (`analytics/themes`). Zero, one, or several tags per ETF; `tags` is empty (never `None`)
    when this ETF has no 主題/因子 assigned on the source page.

    `holder_count_wan` (受益人次, in 萬人/10-thousands) is scraped from the SAME page — the
    microsite's own beneficiary-count stat, refreshed on its own cadence; `None` when the page
    doesn't carry the figure for this symbol (never fabricated)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    tags: tuple[str, ...] = ()
    holder_count_wan: float | None = None


class EtfExpenseInfo(BaseModel):
    """`etf_expense_ratio` — internal-fee (內扣費用) disclosure for one fund/ETF, sourced from
    SITCA's (投信投顧公會) monthly fee-rate report. `management_fee_pct`/`custody_fee_pct` are the
    two disclosed line items; a true total-expense-ratio (內扣總費用率, TER) is approximated as
    their sum via `total_expense_ratio_pct_approx` and DOES NOT include other operating costs, so
    it is a floor, not the audited TER — the caveat says so. Any field is `None` when SITCA does
    not disclose it for this fund (never fabricated)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_ | None = None  # the disclosure month SITCA reported
    management_fee_pct: float | None = None  # 經理費 (%)
    custody_fee_pct: float | None = None  # 保管費 (%)
    total_expense_ratio_pct_approx: float | None = None  # 經理費 + 保管費 (approx TER floor)
    custodian_bank: str | None = None  # 保管機構


class EtfNavRow(BaseModel):
    """`etf_nav` — one ETF's premium/discount-to-NAV snapshot, sourced from TWSE MIS's free,
    keyless, whole-market `all_etf.txt` feed (issuer-published estimated NAV, relayed by TWSE —
    not TWSE's own calculation; see the upstream page's own disclaimer). `premium_discount_pct`
    is TWSE's own published 預估折溢價幅度 figure (`(close_price - estimated_nav) /
    estimated_nav * 100`, confirmed live against several symbols), passed through as-is rather
    than recomputed here, so this row never silently diverges from what investors see on the
    source page. `prior_day_nav` is the fund's last confirmed (non-estimated) NAV — the
    estimated NAV is provisional/intraday and can be revised by the issuer.

    Note: units_outstanding is a leading indicator of unusual creation/redemption activity, not
    itself a premium/discount signal — surfaced for completeness since the upstream feed already
    carries it in the same row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    fund_name: str
    units_outstanding: int | None = None  # 已發行受益權單位數
    units_outstanding_change: int | None = None  # 與前日已發行受益單位差異數
    close_price: float | None = None  # 成交價
    estimated_nav: float | None = None  # 投信或總代理人預估淨值
    premium_discount_pct: float | None = None  # 預估折溢價幅度 (%), TWSE's own published figure
    prior_day_nav: float | None = None  # 前一營業日單位淨值


class EtfAumRow(BaseModel):
    """`etf_aum` — one listed TW ETF's total net assets (資產規模), sourced from Yahoo's own
    `totalAssets` figure (`sources/yfinance/analyst.py::_etf_aum`) rather than derived locally
    from `EtfNavRow.units_outstanding x estimated_nav` — an issuer/exchange-adjacent figure
    Yahoo itself computes, preferred over a locally-multiplied approximation once ingested.
    Per-symbol fan-out (yfinance has no whole-market ETF form), same accepted exception
    `ingest/us_jobs.py::job_us_fundamentals` documents — paced, never a live per-request call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    aum: float | None = None  # 資產規模 (NTD), None when Yahoo has no totalAssets for this fund


class EtfTrailingReturns(BaseModel):
    """`etf_trailing_returns` — one listed ETF's price-return over 6 fixed trailing horizons
    (1個月/1季/半年/1年/3年/5年), computed from ONE split/dividend-adjusted price history fetch
    (`yfinance.Ticker.history(period="5y", auto_adjust=True)`) — never yfinance's own `.info`
    fields (`ytdReturn`/`threeYearAverageReturn`/`fiveYearAverageReturn`), which mix inconsistent
    units (percent vs. fraction, confirmed live) and don't cover 1M/1Q/6M/1Y at all. Computing
    every horizon the same way over the same already-adjusted series means the six numbers are
    genuinely comparable to each other, not stitched from two different methodologies.

    `auto_adjust=True` retroactively folds every split/dividend into the historical close
    series — this is deliberately NOT the raw `Capability.PRICE_EOD` bars this codebase ingests
    elsewhere (those are unadjusted; see `get_fund_comparison`'s own dividend-reinvestment
    simulation, built for exactly that gap, but which itself broke on 0050's real 2025-06-18
    4-for-1 split precisely because the ingested bars are unadjusted — this capability sidesteps
    that whole class of bug by reading Yahoo's pre-adjusted series instead).

    A horizon is `None` (never fabricated) when the 5-year history window doesn't reach back
    far enough for that horizon (e.g. a fund listed 2 years ago has no real 5-year return)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    return_1m_pct: float | None = None
    return_1q_pct: float | None = None
    return_6m_pct: float | None = None
    return_1y_pct: float | None = None
    return_3y_pct: float | None = None
    return_5y_pct: float | None = None


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
    instrument_kind: str  # "stock" | "etf"
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


class TdccSecuritiesInfoRow(BaseModel):
    """`tdcc_securities_info` — one 證券基本資料 (securities master reference) row from TDCC
    OpenData id `1-1`: market/registrar/status/par-value reference facts, not a price."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    name: str
    listing_market: str  # upstream "市場別", e.g. "上市"/"上櫃"/"興櫃"/"終止上市櫃"
    transfer_agent: str  # upstream "股務單位"
    security_status: str  # upstream "證券狀態", e.g. "正常"
    scripless: str | None = None  # upstream "無面無實體" Y/blank
    currency: str
    par_value: float
    updated_date: date_


class TdccPledgedCustodyShareRow(BaseModel):
    """`tdcc_pledged_custody_shares` — one 集保股份保管明細 row from TDCC OpenData id `1-4`:
    the split between dispersed (分戶) vs specific/mandatory (集保) custody for one stock's
    total issued shares, as of the dump's single reported day."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    name: str
    as_of: date_
    dispersed_custody_shares: int  # upstream "分戶保管股數"
    specific_custody_shares: int  # upstream "特定保管股數"
    mandatory_custody_shares: int  # upstream "強制集保總股數"
    issued_thousand_shares: int  # upstream "發行仟股數"


class TdccShareholdingDistributionRow(BaseModel):
    """`tdcc_shareholding_distribution` — one 集保戶股權分散表 (shareholding-level distribution)
    tier row from TDCC OpenData id `1-5`. Public/free, distinct from FinMind's Sponsor-tier
    `shareholding_distribution` capability (`TaiwanStockHoldingSharesPer`) — same concept,
    different source, deliberately not the same `Capability` name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    holding_level: str  # upstream "持股分級" — a numbered tier code (1-17), not itself a range
    holders: int  # upstream "人數"
    shares: int  # upstream "股數"
    percent_of_custody: float  # upstream "占集保庫存數比例%"


class TdccOffshoreFundNavRow(BaseModel):
    """`tdcc_offshore_fund_nav` — one 境外基金淨值 (offshore-fund NAV) row from TDCC OpenData id
    `3-4`. Offshore funds are identified by TDCC's own fund code, not a `Market`-scoped equity
    symbol — `symbol` here is that fund code, `market` is fixed `Market.TW` (the distribution
    channel, not the fund's own domicile) purely to satisfy the shared `(capability, market)`
    routing shape every other TW-side capability in this registry uses."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str  # upstream "基金代碼" — TDCC's own offshore-fund code
    market: Market
    name: str
    nav_date: date_
    nav: float
    isin_code: str | None = None
    currency: str
    offshore_fund_institution: str  # upstream "境外基金機構"
    master_agent: str  # upstream "總代理機構"


class TdccEvotingScheduleRow(BaseModel):
    """`tdcc_evoting_schedule` — one 股東會電子投票起訖日 (shareholder-meeting e-voting window)
    row from TDCC OpenData id `6-2`. Reference/scheduling data, never a vote tally or result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    name: str
    fiscal_year_roc: str  # upstream "年度" — ROC year, kept as the upstream's own reference unit
    meeting_date: date_
    voting_start_date: date_
    voting_end_date: date_


class TdccMonthlyCustodyAccountsRow(BaseModel):
    """`tdcc_monthly_custody_accounts` — one whole-market monthly 集保帳戶統計 row from TDCC
    OpenData id `2-1`: new/closed/total custody-account counts for the month. Market-level, no
    per-symbol scope — the same "reference roster" shape as `MarketOnlyInput`-backed tools
    elsewhere in this registry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    as_of_month: str  # ISO "YYYY-MM", converted from upstream ROC "資料年月"
    new_accounts: int
    closed_accounts: int
    net_accounts: int  # upstream "總開戶數" — net change in the month, not a running total
    cumulative_accounts: int  # upstream "累計開戶數"
