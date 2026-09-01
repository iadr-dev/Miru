"""FinMind Technical-category datasets — EOD/adjusted/tick/weekly/monthly price series, the
stock/warrant reference rosters, the trading calendar, valuation ratios, order-book stats, and
suspension/limit reference streams. 19 datasets.
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
    _row_symbol,
)


def _map_eod_bars(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    for row in rows:
        close = _as_float(row.get("close"))
        if close <= 0:
            # TaiwanStockPrice publishes 0 for no-trade days — filter on close, not volume.
            continue
        row_sym = _row_symbol(row, sym)
        if not row_sym.id[:1].isdigit():
            # The whole-market pull's `stock_id` occasionally carries a FinMind index/sector
            # aggregate label instead of a real security code — confirmed live: `TAIEX`,
            # `TPEx`, and TWSE's ~30 named industry sectors (`Semiconductor`, `Textiles`, …)
            # each occasionally publish a nonzero `close`, which slips past the `close <= 0`
            # filter above since it isn't a no-trade day for the aggregate. Every real TW
            # security code (stock/ETF/warrant/bond) starts with a digit; an aggregate label
            # never does — cheap, exact discriminator, no hardcoded label list to maintain.
            continue
        bars.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "open": _as_float(row.get("open")),
                "high": _as_float(row.get("max")),
                "low": _as_float(row.get("min")),
                "close": close,
                "volume": _as_int(row.get("Trading_Volume")),
                "turnover": row.get("Trading_money"),
            }
        )
    return bars


def _map_reference_roster(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockInfo` / `TaiwanStockInfoWithWarrant` — no `data_id` is ever meaningful for
    these datasets (the whole roster is always returned), so `sym` is never used; each row
    already carries its own `stock_id`."""
    infos: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, None)
        if not row_sym.id[:1].isdigit():
            # Same FinMind whole-market-dump contamination `_map_eod_bars` already filters:
            # `TaiwanStockInfo` mixes in aggregate rows (`TAIEX`, TWSE's ~30 named industry
            # sectors) with `industry_category="Index"`/"大盤" and no real `date` — a literal
            # `None` that previously round-tripped through Parquet as the string `"None"` and
            # failed `StockInfoRow` validation on every read. Every real TW security code starts
            # with a digit; an aggregate label never does.
            continue
        infos.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "name": str(row.get("stock_name", "")),
                "industry_category": str(row.get("industry_category", "")),
                "security_type": str(row.get("type", "")),
                "as_of": row.get("date"),
            }
        )
    return infos


def _map_warrant_underlying(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockInfoWithWarrantSummary` — a warrant code is recycled across issues, so the
    same code can produce multiple rows here, each scoped to its own `[date, end_date]`
    interval; every row is passed through faithfully, never collapsed to one-per-code."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "listed_date": row["date"],
                "close": _as_float(row.get("close")),
                "target_stock_id": str(row.get("target_stock_id", "")),
                "target_close": _as_float(row.get("target_close")),
                "warrant_type": str(row.get("type", "")),
                "fulfillment_method": str(row.get("fulfillment_method", "")),
                "end_date": row.get("end_date"),
                "fulfillment_start_date": row.get("fulfillment_start_date") or None,
                "fulfillment_end_date": row.get("fulfillment_end_date") or None,
                "exercise_ratio": _as_float(row.get("exercise_ratio")),
                "fulfillment_price": _as_float(row.get("fulfillment_price")),
            }
        )
    return rows_out


def _map_trading_calendar(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockTradingDate` — a market-level reference table (no `stock_id` column at
    all)."""
    return [{"market": Market.TW.value, "trade_date": _as_date_str(row["date"])} for row in rows]


def _map_price_adj(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    """`TaiwanStockPriceAdj` — unlike `TaiwanStockPrice`, FinMind carries the previous trading
    day's adjusted price forward on no-published-price days rather than publishing 0, so this
    mapper does not filter on `close` — every upstream row is kept as-is."""
    bars: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        bars.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "open": _as_float(row.get("open")),
                "high": _as_float(row.get("max")),
                "low": _as_float(row.get("min")),
                "close": _as_float(row.get("close")),
                "volume": _as_int(row.get("Trading_Volume")),
                "turnover": row.get("Trading_money"),
            }
        )
    return bars


def _map_price_tick(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    ticks: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        ticks.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "time": str(row.get("Time", "")),
                "deal_price": _as_float(row.get("deal_price")),
                "volume": _as_int(row.get("volume")),
                "tick_type": str(row.get("TickType", "")),
            }
        )
    return ticks


def _map_valuation_ratios(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    ratios: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        ratios.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "dividend_yield": _as_float(row.get("dividend_yield")),
                "per": _as_float(row.get("PER")),
                "pbr": _as_float(row.get("PBR")),
            }
        )
    return ratios


def _map_order_book_stats(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockStatisticsOfOrderBookAndTrade` — a whole-exchange 5-second aggregate; no
    `stock_id` column exists."""
    stats: list[dict[str, object]] = []
    for row in rows:
        stats.append(
            {
                "market": Market.TW.value,
                "trade_date": _as_date_str(row["date"]),
                "time": str(row.get("Time", "")),
                "total_buy_order": _as_int(row.get("TotalBuyOrder")),
                "total_buy_volume": _as_int(row.get("TotalBuyVolume")),
                "total_sell_order": _as_int(row.get("TotalSellOrder")),
                "total_sell_volume": _as_int(row.get("TotalSellVolume")),
                "total_deal_order": _as_int(row.get("TotalDealOrder")),
                "total_deal_volume": _as_int(row.get("TotalDealVolume")),
                "total_deal_money": _as_int(row.get("TotalDealMoney")),
            }
        )
    return stats


def _map_taiex_5s_index(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanVariousIndicators5Seconds` — TAIEX only, no `stock_id` column."""
    return [
        {
            "market": Market.TW.value,
            "trade_date": _as_date_str(row["date"]),
            "taiex": _as_float(row.get("TAIEX")),
        }
        for row in rows
    ]


def _map_day_trading(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "buy_after_sale": str(row.get("BuyAfterSale", "")),
                "volume": _as_int(row.get("Volume")),
                "buy_amount": _as_int(row.get("BuyAmount")),
                "sell_amount": _as_int(row.get("SellAmount")),
            }
        )
    return rows_out


def _map_total_return_index(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockTotalReturnIndex` — `data_id` is one of the pseudo-codes `TAIEX`/`TPEx`, not
    a tradable stock."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "price": _as_float(row.get("price")),
            }
        )
    return rows_out


def _map_ten_year_price(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "close": _as_float(row.get("close")),
            }
        )
    return rows_out


def _map_kbar(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        bars.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "minute": str(row.get("minute", "")),
                "open": _as_float(row.get("open")),
                "high": _as_float(row.get("high")),
                "low": _as_float(row.get("low")),
                "close": _as_float(row.get("close")),
                "volume": _as_float(row.get("volume")),
            }
        )
    return bars


def _make_period_price_mapper(
    period_field: str,
) -> Callable[[list[dict[str, object]], Symbol | None], list[dict[str, object]]]:
    """`TaiwanStockWeekPrice`/`TaiwanStockMonthPrice` share an identical shape aside from the
    period-label column (`yweek` vs `ymonth`) — one parameterized mapper for both."""

    def _mapper(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
        rows_out: list[dict[str, object]] = []
        for row in rows:
            row_sym = _row_symbol(row, sym)
            rows_out.append(
                {
                    "symbol": row_sym.id,
                    "market": row_sym.market.value,
                    "period": str(row.get(period_field, "")),
                    "trade_date": _as_date_str(row["date"]),
                    "open": _as_float(row.get("open")),
                    "high": _as_float(row.get("max")),
                    "low": _as_float(row.get("min")),
                    "close": _as_float(row.get("close")),
                    "trading_volume": _as_int(row.get("trading_volume")),
                    "trading_money": _as_int(row.get("trading_money")),
                    "trading_turnover": _as_float(row.get("trading_turnover")),
                }
            )
        return rows_out

    return _mapper


_map_price_weekly = _make_period_price_mapper("yweek")
_map_price_monthly = _make_period_price_mapper("ymonth")


def _map_index_5s_snapshot(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanStockEvery5SecondsIndex` — no `data_id` filter is documented; the full set of
    index readings comes back regardless, so each row's own `stock_id` is used."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, None)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "time": str(row.get("time", "")),
                "price": _as_float(row.get("price")),
                "kind": str(row.get("kind", "")),
            }
        )
    return rows_out


def _map_trading_suspension(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "date": _as_date_str(row["date"]),
                "suspension_time": str(row.get("suspension_time", "")),
                "resumption_date": row.get("resumption_date") or None,
                "resumption_time": row.get("resumption_time") or None,
            }
        )
    return rows_out


def _map_day_trading_suspension(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "date": _as_date_str(row["date"]),
                "end_date": row.get("end_date"),
                "reason": str(row.get("reason", "")),
            }
        )
    return rows_out


def _map_price_limit(rows: list[dict[str, object]], sym: Symbol | None) -> list[dict[str, object]]:
    """`TaiwanStockPriceLimit` — `limit_up`/`limit_down` of 0 means no price-change limit
    applies (leveraged/inverse ETFs, emerging-board stocks) — not a data gap."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol(row, sym)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "reference_price": _as_float(row.get("reference_price")),
                "limit_up": _as_float(row.get("limit_up")),
                "limit_down": _as_float(row.get("limit_down")),
            }
        )
    return rows_out


DATASETS: dict[Capability, _DatasetConfig] = {
    Capability.PRICE_EOD: _DatasetConfig(
        dataset="TaiwanStockPrice",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="FinMind T+1 EOD aggregator — not a live quote; updates Mon-Fri ~17:30.",
        row_key="bars",
        row_mapper=_map_eod_bars,
        whole_market_ok=True,
    ),
    Capability.STOCK_INFO: _DatasetConfig(
        dataset="TaiwanStockInfo",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="Whole-roster reference table (stock universe); no `data_id` is ever meaningful "
        "— the full list always comes back.",
        row_key="rows",
        row_mapper=_map_reference_roster,
        whole_market_ok=True,
    ),
    Capability.STOCK_INFO_WITH_WARRANT: _DatasetConfig(
        dataset="TaiwanStockInfoWithWarrant",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="Whole-roster reference table (stock + warrant universe); no `data_id` is ever "
        "meaningful — the full list always comes back.",
        row_key="rows",
        row_mapper=_map_reference_roster,
        whole_market_ok=True,
    ),
    Capability.WARRANT_UNDERLYING: _DatasetConfig(
        dataset="TaiwanStockInfoWithWarrantSummary",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.REFERENCE,
        caveat="Sponsor-tier warrant-to-underlying mapping table; a warrant code can be "
        "recycled to a different underlying across issues — resolve by date interval, not "
        "the latest row.",
        row_key="rows",
        row_mapper=_map_warrant_underlying,
        whole_market_ok=True,
    ),
    Capability.TRADING_CALENDAR: _DatasetConfig(
        dataset="TaiwanStockTradingDate",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="Whole-market trading-day calendar reference table; no `data_id` param exists.",
        row_key="rows",
        row_mapper=_map_trading_calendar,
        whole_market_ok=True,
    ),
    Capability.PRICE_EOD_ADJUSTED: _DatasetConfig(
        dataset="TaiwanStockPriceAdj",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Back-adjusted EOD price series; carries the previous trading day's price "
        "forward on no-published-price days rather than 0 — cross-check TaiwanStockPrice to "
        "find which days had no published price. Updates Mon-Fri ~17:30.",
        row_key="bars",
        row_mapper=_map_price_adj,
        whole_market_ok=True,
    ),
    Capability.PRICE_TICK: _DatasetConfig(
        dataset="TaiwanStockPriceTick",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier tick-level trade history; single trading day per request, "
        "published ~15:30 the same day, not a live feed. TickType is unreliable (always "
        "buyer-initiated) for all 2019 data.",
        row_key="ticks",
        row_mapper=_map_price_tick,
    ),
    Capability.VALUATION_RATIOS: _DatasetConfig(
        dataset="TaiwanStockPER",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Daily PER/PBR/dividend-yield; published post-close ~18:00, not intraday.",
        row_key="rows",
        row_mapper=_map_valuation_ratios,
    ),
    Capability.ORDER_BOOK_STATS: _DatasetConfig(
        dataset="TaiwanStockStatisticsOfOrderBookAndTrade",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Whole-exchange 5-second order-book/trade aggregate; single trading day per "
        "request, no `data_id` param exists.",
        row_key="stats",
        row_mapper=_map_order_book_stats,
        whole_market_ok=True,
    ),
    Capability.TAIEX_5S_INDEX: _DatasetConfig(
        dataset="TaiwanVariousIndicators5Seconds",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Whole-market TAIEX 5-second index reading; single trading day per request, no "
        "`data_id` param exists.",
        row_key="rows",
        row_mapper=_map_taiex_5s_index,
        whole_market_ok=True,
    ),
    Capability.DAY_TRADING: _DatasetConfig(
        dataset="TaiwanStockDayTrading",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Day-trading eligibility marker + volume/value; eligibility list is pre-market "
        "same day, volume/value updates post-close ~21:30.",
        row_key="rows",
        row_mapper=_map_day_trading,
        whole_market_ok=True,
    ),
    Capability.TOTAL_RETURN_INDEX: _DatasetConfig(
        dataset="TaiwanStockTotalReturnIndex",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="TAIEX/TPEx total-return index; `data_id` is a pseudo-code (TAIEX/TPEx), not a "
        "tradable stock. Updates Mon-Fri ~16:50.",
        row_key="rows",
        row_mapper=_map_total_return_index,
    ),
    Capability.TEN_YEAR_PRICE: _DatasetConfig(
        dataset="TaiwanStock10Year",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier ten-year price line; updates Mon-Fri ~20:00.",
        row_key="rows",
        row_mapper=_map_ten_year_price,
        whole_market_ok=True,
    ),
    Capability.INTRADAY_KBAR: _DatasetConfig(
        dataset="TaiwanStockKBar",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Sponsor-tier minute-bar series; single trading day per request, published "
        "~15:50 the same day, not a live feed.",
        row_key="bars",
        row_mapper=_map_kbar,
    ),
    Capability.PRICE_WEEKLY: _DatasetConfig(
        dataset="TaiwanStockWeekPrice",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier weekly bar series; updates Mon-Fri ~17:30.",
        row_key="bars",
        row_mapper=_map_price_weekly,
        whole_market_ok=True,
    ),
    Capability.PRICE_MONTHLY: _DatasetConfig(
        dataset="TaiwanStockMonthPrice",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier monthly bar series; updates Mon-Fri ~17:30.",
        row_key="bars",
        row_mapper=_map_price_monthly,
        whole_market_ok=True,
        # One bar per calendar month — a 30-day window can straddle a month boundary and miss
        # the only bar in range (confirmed empirically: 0 rows at 30d for 2330).
        default_lookback_days=120,
    ),
    Capability.INDEX_5S_SNAPSHOT: _DatasetConfig(
        dataset="TaiwanStockEvery5SecondsIndex",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Backer/Sponsor-tier 5-second index snapshot; single trading day per request, "
        "no `data_id` filter is documented — the full set of index readings comes back "
        "regardless.",
        row_key="rows",
        row_mapper=_map_index_5s_snapshot,
        whole_market_ok=True,
    ),
    Capability.TRADING_SUSPENSION: _DatasetConfig(
        dataset="TaiwanStockSuspended",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Backer/Sponsor-tier trading-halt announcement stream; event-driven, not a "
        "daily-clock-time market snapshot.",
        row_key="rows",
        row_mapper=_map_trading_suspension,
        whole_market_ok=True,
        default_lookback_days=450,  # rare event — a 30d window usually has no occurrence.
    ),
    Capability.DAY_TRADING_SUSPENSION: _DatasetConfig(
        dataset="TaiwanStockDayTradingSuspension",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat="Backer/Sponsor-tier sell-first-day-trading-suspension announcement stream; "
        "event-driven, not a daily-clock-time market snapshot.",
        row_key="rows",
        row_mapper=_map_day_trading_suspension,
        whole_market_ok=True,
        # Rare, irregular per-symbol event — a 30-day window frequently has no occurrence at
        # all (confirmed empirically: 0 rows at 30d for 2330).
        default_lookback_days=450,
    ),
    Capability.PRICE_LIMIT: _DatasetConfig(
        dataset="TaiwanStockPriceLimit",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        caveat="Daily reference/limit-up/limit-down price; updates Mon-Fri ~18:00. A 0 "
        "limit_up/limit_down means no price-change limit applies, not missing data.",
        row_key="rows",
        row_mapper=_map_price_limit,
        whole_market_ok=True,
    ),
}

__all__ = ["DATASETS"]
