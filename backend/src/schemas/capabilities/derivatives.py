"""Futures and options rows, plus the live tick/futures/options snapshots."""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict

from schemas.enums import Market


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
