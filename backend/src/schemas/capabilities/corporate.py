"""Corporate actions and per-company reference rows."""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict

from schemas.enums import Market


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
