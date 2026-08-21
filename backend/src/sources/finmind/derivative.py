"""FinMind Derivative-category datasets — futures/options contract EOD/tick series, the
three-major-institutional-investor futures/options flow (day and after-hours sessions), dealer
trading volume, open-interest large-trader concentration, spread trading, final settlement
prices, the TAIEX option VIX, and asset-swap daily trading. 20 datasets.
"""

from __future__ import annotations

from collections.abc import Callable

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol

from ._shared import (
    _as_date_str,
    _as_float,
    _as_int,
    _DatasetConfig,
    _row_derivative_symbol,
    _row_symbol,
)


def _map_futopt_info(rows: list[dict[str, object]], _sym: Symbol | None) -> list[dict[str, object]]:
    """`TaiwanFutOptDailyInfo` — a static futures/options contract-code roster, not a per-date
    time series; modeled the same way as the reference-roster datasets."""
    return [
        {
            "market": Market.TW.value,
            "code": str(row.get("code", "")),
            "contract_type": str(row.get("type", "")),
            "name": str(row.get("name", "")),
        }
        for row in rows
    ]


def _map_futures_daily(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_derivative_symbol(row, sym, "futures_id")
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "contract_date": str(row.get("contract_date", "")),
                "open": _as_float(row.get("open")),
                "high": _as_float(row.get("max")),
                "low": _as_float(row.get("min")),
                "close": _as_float(row.get("close")),
                "spread": _as_float(row.get("spread")),
                "spread_per": _as_float(row.get("spread_per")),
                "volume": _as_int(row.get("volume")),
                "settlement_price": _as_float(row.get("settlement_price")),
                "open_interest": _as_int(row.get("open_interest")),
                "trading_session": str(row.get("trading_session", "")),
            }
        )
    return rows_out


def _map_option_daily(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_derivative_symbol(row, sym, "option_id")
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "contract_date": str(row.get("contract_date", "")),
                "strike_price": _as_float(row.get("strike_price")),
                "call_put": str(row.get("call_put", "")),
                "open": _as_float(row.get("open")),
                "high": _as_float(row.get("max")),
                "low": _as_float(row.get("min")),
                "close": _as_float(row.get("close")),
                "volume": _as_int(row.get("volume")),
                "settlement_price": _as_float(row.get("settlement_price")),
                "open_interest": _as_int(row.get("open_interest")),
                "trading_session": str(row.get("trading_session", "")),
            }
        )
    return rows_out


def _map_futures_tick(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    """`TaiwanFuturesTick` — tick volume is double-sided (~2x `futures_daily`'s single-side
    volume; ~4x for spread/combination orders) and the after-hours session is attributed to the
    *next* trading day (TAIFEX convention) — both quirks are upstream data conventions, passed
    through unchanged rather than corrected here."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_derivative_symbol(row, sym, "futures_id")
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "contract_date": str(row.get("contract_date", "")),
                "price": _as_float(row.get("price")),
                "volume": _as_int(row.get("volume")),
            }
        )
    return rows_out


def _map_futures_spread_tick(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanFuturesSpreadTick` — `contract_date` carries both legs of the calendar spread
    (e.g. `202606/202607`); `price` is the spread price and can be negative."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_derivative_symbol(row, sym, "futures_id")
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "time": str(row.get("time", "")),
                "contract_date": str(row.get("contract_date", "")),
                "price": _as_float(row.get("price")),
                "volume": _as_int(row.get("volume")),
                "near_price": _as_float(row.get("near_price")),
                "far_price": _as_float(row.get("far_price")),
                "spread_to_spread": _as_int(row.get("spread_to_spread")),
            }
        )
    return rows_out


def _map_option_tick(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_derivative_symbol(row, sym, "option_id")
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "contract_date": str(row.get("contract_date", "")),
                "exercise_price": _as_float(row.get("ExercisePrice")),
                "put_call": str(row.get("PutCall", "")),
                "price": _as_float(row.get("price")),
                "volume": _as_int(row.get("volume")),
            }
        )
    return rows_out


def _map_futures_institutional_flow(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanFuturesInstitutionalInvestors` — the upstream row shape has no per-contract id
    column at all (`name`/`institutional_investors`/flow figures only), so on a whole-market
    call `symbol` is left empty rather than guessed."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = sym if sym is not None else Symbol(id="", market=Market.TW)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "investor": str(row.get("institutional_investors", "")),
                "long_deal_volume": _as_int(row.get("long_deal_volume")),
                "long_deal_amount": _as_int(row.get("long_deal_amount")),
                "short_deal_volume": _as_int(row.get("short_deal_volume")),
                "short_deal_amount": _as_int(row.get("short_deal_amount")),
                "long_open_interest_balance_volume": _as_int(
                    row.get("long_open_interest_balance_volume")
                ),
                "long_open_interest_balance_amount": _as_int(
                    row.get("long_open_interest_balance_amount")
                ),
                "short_open_interest_balance_volume": _as_int(
                    row.get("short_open_interest_balance_volume")
                ),
                "short_open_interest_balance_amount": _as_int(
                    row.get("short_open_interest_balance_amount")
                ),
            }
        )
    return rows_out


def _map_option_institutional_flow(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanOptionInstitutionalInvestors` — same no-contract-id-column gap as the futures
    variant; `symbol` is left empty on whole-market calls rather than guessed."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = sym if sym is not None else Symbol(id="", market=Market.TW)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "call_put": str(row.get("call_put", "")),
                "investor": str(row.get("institutional_investors", "")),
                "long_deal_volume": _as_int(row.get("long_deal_volume")),
                "long_deal_amount": _as_int(row.get("long_deal_amount")),
                "short_deal_volume": _as_int(row.get("short_deal_volume")),
                "short_deal_amount": _as_int(row.get("short_deal_amount")),
                "long_open_interest_balance_volume": _as_int(
                    row.get("long_open_interest_balance_volume")
                ),
                "long_open_interest_balance_amount": _as_int(
                    row.get("long_open_interest_balance_amount")
                ),
                "short_open_interest_balance_volume": _as_int(
                    row.get("short_open_interest_balance_volume")
                ),
                "short_open_interest_balance_amount": _as_int(
                    row.get("short_open_interest_balance_amount")
                ),
            }
        )
    return rows_out


def _map_futures_institutional_flow_after_hours(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_derivative_symbol(row, sym, "futures_id")
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "investor": str(row.get("institutional_investors", "")),
                "long_deal_volume": _as_int(row.get("long_deal_volume")),
                "long_deal_amount": _as_int(row.get("long_deal_amount")),
                "short_deal_volume": _as_int(row.get("short_deal_volume")),
                "short_deal_amount": _as_int(row.get("short_deal_amount")),
            }
        )
    return rows_out


def _map_option_institutional_flow_after_hours(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_derivative_symbol(row, sym, "option_id")
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "call_put": str(row.get("call_put", "")),
                "investor": str(row.get("institutional_investors", "")),
                "long_deal_volume": _as_int(row.get("long_deal_volume")),
                "long_deal_amount": _as_int(row.get("long_deal_amount")),
                "short_deal_volume": _as_int(row.get("short_deal_volume")),
                "short_deal_amount": _as_int(row.get("short_deal_amount")),
            }
        )
    return rows_out


def _make_dealer_volume_mapper(
    id_field: str,
) -> Callable[[list[dict[str, object]], Symbol | None], list[dict[str, object]]]:
    """`TaiwanFuturesDealerTradingVolumeDaily`/`TaiwanOptionDealerTradingVolumeDaily` share an
    identical shape aside from the contract-id column name."""

    def _mapper(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
        rows_out: list[dict[str, object]] = []
        for row in rows:
            row_sym = _row_derivative_symbol(row, sym, id_field)
            rows_out.append(
                {
                    "symbol": row_sym.id,
                    "market": row_sym.market.value,
                    "trade_date": _as_date_str(row["date"]),
                    "dealer_code": str(row.get("dealer_code", "")),
                    "dealer_name": str(row.get("dealer_name", "")),
                    "volume": _as_int(row.get("volume")),
                    "is_after_hour": bool(_as_int(row.get("is_after_hour"))),
                }
            )
        return rows_out

    return _mapper


_map_futures_dealer_volume = _make_dealer_volume_mapper("futures_id")
_map_option_dealer_volume = _make_dealer_volume_mapper("option_id")


def _map_futures_oi_large_traders(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_derivative_symbol(row, sym, "futures_id")
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "name": str(row.get("name", "")),
                "contract_type": str(row.get("contract_type", "")),
                "buy_top5_trader_open_interest": _as_int(row.get("buy_top5_trader_open_interest")),
                "buy_top5_trader_open_interest_per": _as_float(
                    row.get("buy_top5_trader_open_interest_per")
                ),
                "buy_top10_trader_open_interest": _as_int(
                    row.get("buy_top10_trader_open_interest")
                ),
                "buy_top10_trader_open_interest_per": _as_float(
                    row.get("buy_top10_trader_open_interest_per")
                ),
                "sell_top5_trader_open_interest": _as_int(
                    row.get("sell_top5_trader_open_interest")
                ),
                "sell_top5_trader_open_interest_per": _as_float(
                    row.get("sell_top5_trader_open_interest_per")
                ),
                "sell_top10_trader_open_interest": _as_int(
                    row.get("sell_top10_trader_open_interest")
                ),
                "sell_top10_trader_open_interest_per": _as_float(
                    row.get("sell_top10_trader_open_interest_per")
                ),
                "market_open_interest": _as_int(row.get("market_open_interest")),
                "buy_top5_specific_open_interest": _as_int(
                    row.get("buy_top5_specific_open_interest")
                ),
                "buy_top5_specific_open_interest_per": _as_float(
                    row.get("buy_top5_specific_open_interest_per")
                ),
                "buy_top10_specific_open_interest": _as_int(
                    row.get("buy_top10_specific_open_interest")
                ),
                "buy_top10_specific_open_interest_per": _as_float(
                    row.get("buy_top10_specific_open_interest_per")
                ),
                "sell_top5_specific_open_interest": _as_int(
                    row.get("sell_top5_specific_open_interest")
                ),
                "sell_top5_specific_open_interest_per": _as_float(
                    row.get("sell_top5_specific_open_interest_per")
                ),
                "sell_top10_specific_open_interest": _as_int(
                    row.get("sell_top10_specific_open_interest")
                ),
                "sell_top10_specific_open_interest_per": _as_float(
                    row.get("sell_top10_specific_open_interest_per")
                ),
            }
        )
    return rows_out


def _map_option_oi_large_traders(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanOptionOpenInterestLargeTraders` — split by `put_call`, the put/call large-trader
    concentration breakdown carried straight through from upstream."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_derivative_symbol(row, sym, "option_id")
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "put_call": str(row.get("put_call", "")),
                "name": str(row.get("name", "")),
                "contract_type": str(row.get("contract_type", "")),
                "buy_top5_trader_open_interest": _as_int(row.get("buy_top5_trader_open_interest")),
                "buy_top5_trader_open_interest_per": _as_float(
                    row.get("buy_top5_trader_open_interest_per")
                ),
                "buy_top10_trader_open_interest": _as_int(
                    row.get("buy_top10_trader_open_interest")
                ),
                "buy_top10_trader_open_interest_per": _as_float(
                    row.get("buy_top10_trader_open_interest_per")
                ),
                "sell_top5_trader_open_interest": _as_int(
                    row.get("sell_top5_trader_open_interest")
                ),
                "sell_top5_trader_open_interest_per": _as_float(
                    row.get("sell_top5_trader_open_interest_per")
                ),
                "sell_top10_trader_open_interest": _as_int(
                    row.get("sell_top10_trader_open_interest")
                ),
                "sell_top10_trader_open_interest_per": _as_float(
                    row.get("sell_top10_trader_open_interest_per")
                ),
                "market_open_interest": _as_int(row.get("market_open_interest")),
                "buy_top5_specific_open_interest": _as_int(
                    row.get("buy_top5_specific_open_interest")
                ),
                "buy_top5_specific_open_interest_per": _as_float(
                    row.get("buy_top5_specific_open_interest_per")
                ),
                "buy_top10_specific_open_interest": _as_int(
                    row.get("buy_top10_specific_open_interest")
                ),
                "buy_top10_specific_open_interest_per": _as_float(
                    row.get("buy_top10_specific_open_interest_per")
                ),
                "sell_top5_specific_open_interest": _as_int(
                    row.get("sell_top5_specific_open_interest")
                ),
                "sell_top5_specific_open_interest_per": _as_float(
                    row.get("sell_top5_specific_open_interest_per")
                ),
                "sell_top10_specific_open_interest": _as_int(
                    row.get("sell_top10_specific_open_interest")
                ),
                "sell_top10_specific_open_interest_per": _as_float(
                    row.get("sell_top10_specific_open_interest_per")
                ),
            }
        )
    return rows_out


def _map_futures_spread_trading(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_derivative_symbol(row, sym, "futures_id")
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "contract_date": str(row.get("contract_date", "")),
                "open": _as_float(row.get("open")),
                "high": _as_float(row.get("max")),
                "low": _as_float(row.get("min")),
                "close": _as_float(row.get("close")),
                "best_bid": _as_float(row.get("best_bid")),
                "best_ask": _as_float(row.get("best_ask")),
                "historical_max": _as_float(row.get("historical_max")),
                "historical_min": _as_float(row.get("historical_min")),
                "spread_to_spread_volume": _as_int(row.get("spread_to_spread_volume")),
                "spread_to_single_volume": _as_int(row.get("spread_to_single_volume")),
                "trading_session": str(row.get("trading_session", "")),
            }
        )
    return rows_out


def _make_final_settlement_price_mapper(
    id_field: str, type_field: str, name_field: str
) -> Callable[[list[dict[str, object]], Symbol | None], list[dict[str, object]]]:
    """`TaiwanFuturesFinalSettlementPrice`/`TaiwanOptionFinalSettlementPrice` are structurally
    near-identical aside from the contract-id/type/name column names."""

    def _mapper(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
        rows_out: list[dict[str, object]] = []
        for row in rows:
            row_sym = _row_derivative_symbol(row, sym, id_field)
            rows_out.append(
                {
                    "symbol": row_sym.id,
                    "market": row_sym.market.value,
                    "trade_date": _as_date_str(row["date"]),
                    "contract_month": str(row.get("contract_month", "")),
                    "instrument_type": str(row.get(type_field, "")),
                    "instrument_name": str(row.get(name_field, "")),
                    "settlement_price": _as_float(row.get("settlement_price")),
                    "underlying_code": str(row.get("underlying_code", "")),
                    "notional_value": _as_float(row.get("notional_value")),
                }
            )
        return rows_out

    return _mapper


_map_futures_final_settlement_price = _make_final_settlement_price_mapper(
    "futures_id", "futures_type", "futures_name"
)
_map_option_final_settlement_price = _make_final_settlement_price_mapper(
    "option_id", "option_type", "option_name"
)


def _map_option_vix(rows: list[dict[str, object]], _sym: Symbol | None) -> list[dict[str, object]]:
    """`TaiwanOptionVix` — market-level, no `stock_id`/`futures_id`/`option_id` column."""
    return [
        {
            "market": Market.TW.value,
            "trade_date": _as_date_str(row["date"]),
            "time": str(row.get("time", "")),
            "vix": _as_float(row.get("vix")),
        }
        for row in rows
    ]


def _make_asset_swap_mapper(
    price_field_prefix: str,
) -> Callable[[list[dict[str, object]], Symbol | None], list[dict[str, object]]]:
    """`TaiwanAssetSwapFixedIncomeDaily`/`TaiwanAssetSwapOptionDaily` share an identical shape
    aside from the rate/premium price-field names (`rate_*` vs `premium_*`)."""

    def _mapper(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
        rows_out: list[dict[str, object]] = []
        for row in rows:
            row_sym = _row_symbol(row, sym)
            rows_out.append(
                {
                    "symbol": row_sym.id,
                    "market": row_sym.market.value,
                    "trade_date": _as_date_str(row["date"]),
                    "stock_name": str(row.get("stock_name", "")),
                    "notional_amount": _as_int(row.get("notional_amount")),
                    "number_of_transactions": _as_int(row.get("number_of_transactions")),
                    f"{price_field_prefix}_lowest": _as_float(
                        row.get(f"{price_field_prefix}_lowest")
                    ),
                    f"{price_field_prefix}_highest": _as_float(
                        row.get(f"{price_field_prefix}_highest")
                    ),
                    f"{price_field_prefix}_average": _as_float(
                        row.get(f"{price_field_prefix}_average")
                    ),
                    "contract_term_years": _as_float(row.get("contract_term_years")),
                }
            )
        return rows_out

    return _mapper


_map_asset_swap_fixed_income_daily = _make_asset_swap_mapper("rate")
_map_asset_swap_option_daily = _make_asset_swap_mapper("premium")


DATASETS: dict[Capability, _DatasetConfig] = {
    Capability.FUTOPT_INFO: _DatasetConfig(
        dataset="TaiwanFutOptDailyInfo",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="Whole-roster futures/options contract-code reference table; a static list, "
        "not a per-date time series — no `data_id` param exists.",
        row_key="rows",
        row_mapper=_map_futopt_info,
        whole_market_ok=True,
    ),
    Capability.FUTURES_DAILY: _DatasetConfig(
        dataset="TaiwanFuturesDaily",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Futures contract EOD price/volume/open-interest row.",
        row_key="rows",
        row_mapper=_map_futures_daily,
        whole_market_ok=True,
    ),
    Capability.OPTION_DAILY: _DatasetConfig(
        dataset="TaiwanOptionDaily",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Option contract EOD price/volume/open-interest row.",
        row_key="rows",
        row_mapper=_map_option_daily,
        whole_market_ok=True,
    ),
    Capability.FUTURES_TICK: _DatasetConfig(
        dataset="TaiwanFuturesTick",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier futures per-trade tick; single trading day per request. "
        "Tick volume is double-sided (~2x TaiwanFuturesDaily's single-side volume, ~4x for "
        "spread orders); the after-hours session is attributed to the next trading day per "
        "TAIFEX convention.",
        row_key="ticks",
        row_mapper=_map_futures_tick,
    ),
    Capability.FUTURES_SPREAD_TICK: _DatasetConfig(
        dataset="TaiwanFuturesSpreadTick",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Sponsor-tier futures calendar-spread per-trade tick; single trading day per "
        "request. `contract_date` carries both legs; `price` is the spread price and can be "
        "negative.",
        row_key="ticks",
        row_mapper=_map_futures_spread_tick,
    ),
    Capability.OPTION_TICK: _DatasetConfig(
        dataset="TaiwanOptionTick",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier option per-trade tick; single trading day per request.",
        row_key="ticks",
        row_mapper=_map_option_tick,
    ),
    Capability.FUTURES_INSTITUTIONAL_FLOW: _DatasetConfig(
        dataset="TaiwanFuturesInstitutionalInvestors",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Three-major-institutional-investor futures buy/sell + open-interest-balance "
        "row. Upstream carries no per-contract id column, so `symbol` is empty on a "
        "whole-market call.",
        row_key="rows",
        row_mapper=_map_futures_institutional_flow,
        whole_market_ok=True,
    ),
    Capability.OPTION_INSTITUTIONAL_FLOW: _DatasetConfig(
        dataset="TaiwanOptionInstitutionalInvestors",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Three-major-institutional-investor option buy/sell + open-interest-balance "
        "row, split by call/put. Upstream carries no per-contract id column, so `symbol` is "
        "empty on a whole-market call.",
        row_key="rows",
        row_mapper=_map_option_institutional_flow,
        whole_market_ok=True,
    ),
    Capability.FUTURES_INSTITUTIONAL_FLOW_AFTER_HOURS: _DatasetConfig(
        dataset="TaiwanFuturesInstitutionalInvestorsAfterHours",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier night-session three-major-institutional-investor futures "
        "buy/sell row; no open-interest-balance breakdown (unlike the day-session dataset).",
        row_key="rows",
        row_mapper=_map_futures_institutional_flow_after_hours,
        whole_market_ok=True,
    ),
    Capability.OPTION_INSTITUTIONAL_FLOW_AFTER_HOURS: _DatasetConfig(
        dataset="TaiwanOptionInstitutionalInvestorsAfterHours",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier night-session three-major-institutional-investor option "
        "buy/sell row, split by call/put; no open-interest-balance breakdown.",
        row_key="rows",
        row_mapper=_map_option_institutional_flow_after_hours,
        whole_market_ok=True,
    ),
    Capability.FUTURES_DEALER_VOLUME: _DatasetConfig(
        dataset="TaiwanFuturesDealerTradingVolumeDaily",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Per-dealer futures trading-volume row.",
        row_key="rows",
        row_mapper=_map_futures_dealer_volume,
        whole_market_ok=True,
    ),
    Capability.OPTION_DEALER_VOLUME: _DatasetConfig(
        dataset="TaiwanOptionDealerTradingVolumeDaily",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Per-dealer option trading-volume row.",
        row_key="rows",
        row_mapper=_map_option_dealer_volume,
        whole_market_ok=True,
    ),
    Capability.FUTURES_OI_LARGE_TRADERS: _DatasetConfig(
        dataset="TaiwanFuturesOpenInterestLargeTraders",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier large-trader open-interest-concentration row for one "
        "futures contract type.",
        row_key="rows",
        row_mapper=_map_futures_oi_large_traders,
        whole_market_ok=True,
    ),
    Capability.OPTION_OI_LARGE_TRADERS: _DatasetConfig(
        dataset="TaiwanOptionOpenInterestLargeTraders",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier large-trader open-interest-concentration row for one "
        "option contract type, split by put/call.",
        row_key="rows",
        row_mapper=_map_option_oi_large_traders,
        whole_market_ok=True,
    ),
    Capability.FUTURES_SPREAD_TRADING: _DatasetConfig(
        dataset="TaiwanFuturesSpreadTrading",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier futures calendar-spread quote row. No documented "
        "omit-data_id whole-market form for this dataset.",
        row_key="rows",
        row_mapper=_map_futures_spread_trading,
    ),
    Capability.FUTURES_FINAL_SETTLEMENT_PRICE: _DatasetConfig(
        dataset="TaiwanFuturesFinalSettlementPrice",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Backer/Sponsor-tier futures final-settlement-price row; event-driven "
        "(settlement date), not a daily-clock-time market snapshot. No documented "
        "omit-data_id whole-market form for this dataset.",
        row_key="rows",
        row_mapper=_map_futures_final_settlement_price,
    ),
    Capability.OPTION_FINAL_SETTLEMENT_PRICE: _DatasetConfig(
        dataset="TaiwanOptionFinalSettlementPrice",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Backer/Sponsor-tier option final-settlement-price row; event-driven "
        "(settlement date), not a daily-clock-time market snapshot. No documented "
        "omit-data_id whole-market form for this dataset.",
        row_key="rows",
        row_mapper=_map_option_final_settlement_price,
    ),
    Capability.OPTION_VIX: _DatasetConfig(
        dataset="TaiwanOptionVix",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier TAIEX option volatility-index reading; market-level, no "
        "`data_id` param exists. Updates Mon-Sat ~18:00.",
        row_key="rows",
        row_mapper=_map_option_vix,
        whole_market_ok=True,
        # Confirmed empirically: 400s ("data_id don't provide") if data_id is sent at all, even
        # on the per-symbol fetch() path — this dataset has no per-contract filter.
        no_data_id_param=True,
    ),
    Capability.ASSET_SWAP_FIXED_INCOME_DAILY: _DatasetConfig(
        dataset="TaiwanAssetSwapFixedIncomeDaily",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier fixed-income asset-swap daily trading row. No documented "
        "omit-data_id whole-market form for this dataset. Updates Mon-Fri 22:00-24:00.",
        row_key="rows",
        row_mapper=_map_asset_swap_fixed_income_daily,
    ),
    Capability.ASSET_SWAP_OPTION_DAILY: _DatasetConfig(
        dataset="TaiwanAssetSwapOptionDaily",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier option asset-swap daily trading row. No documented "
        "omit-data_id whole-market form for this dataset. Updates Mon-Fri 22:00-24:00.",
        row_key="rows",
        row_mapper=_map_asset_swap_option_daily,
    ),
}

__all__ = ["DATASETS"]
