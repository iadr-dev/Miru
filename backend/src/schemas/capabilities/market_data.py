"""Calendar, ticks, order book, index and period price rows."""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict

from schemas.enums import Market


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
