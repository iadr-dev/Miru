"""Chip flows (margin/short/institutional/lending) and risk-list rows."""

from __future__ import annotations

from datetime import date as date_
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import Market
from schemas.source import HealthStatus


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

    rank: int = 0  # 1-based position within this card's bucket
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
    margin_health_status: HealthStatus | None = None
    bucket: Literal["at_risk", "in_disposition", "exiting_soon"]
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
