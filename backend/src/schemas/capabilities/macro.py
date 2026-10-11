"""News, business indicators, international prices, FX, rates and macro series."""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict

from schemas.enums import Market


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
