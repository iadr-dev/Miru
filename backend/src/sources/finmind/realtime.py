"""FinMind Real-Time-category datasets — bounded intraday snapshots for stocks/indices,
futures, and options, plus the futures/options real-time-quote contract reference roster. All
timing-classed `LIVE_ISH`: a single bounded observation, never a live tick stream. 4 datasets.
"""

from __future__ import annotations

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol

from ._shared import _as_date_str, _as_float, _as_int, _DatasetConfig, _row_symbol_field


def _map_tick_snapshot(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    """`taiwan_stock_tick_snapshot` — `data_id` accepts either a 4-digit stock id or a 3-digit
    index code (91 documented codes spanning broad-market/sector/themed/leverage indices); this
    mapper does not restrict or validate the id shape, it passes whichever id the row carries."""
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol_field(row, sym, "stock_id", Market.TW)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "amount": _as_float(row.get("amount")),
                "average_price": _as_float(row.get("average_price")),
                "buy_price": _as_float(row.get("buy_price")),
                "buy_volume": _as_int(row.get("buy_volume")),
                "change_price": _as_float(row.get("change_price")),
                "change_rate": _as_float(row.get("change_rate")),
                "close": _as_float(row.get("close")),
                "high": _as_float(row.get("high")),
                "low": _as_float(row.get("low")),
                "open": _as_float(row.get("open")),
                "sell_price": _as_float(row.get("sell_price")),
                "sell_volume": _as_int(row.get("sell_volume")),
                "total_amount": _as_float(row.get("total_amount")),
                "total_volume": _as_int(row.get("total_volume")),
                "volume": _as_int(row.get("volume")),
                "volume_ratio": _as_float(row.get("volume_ratio")),
                "yesterday_volume": _as_int(row.get("yesterday_volume")),
                "tick_type": str(row.get("TickType", "")),
            }
        )
    return rows_out


def _map_futopt_tick_info(
    rows: list[dict[str, object]], _sym: Symbol | None
) -> list[dict[str, object]]:
    """`TaiwanFutOptTickInfo` — a static futures/options real-time-quote contract roster, not a
    per-date time series; no `data_id` param exists."""
    return [
        {
            "market": Market.TW.value,
            "code": str(row.get("code", "")),
            "call_put": str(row.get("callput", "")),
            "as_of": row.get("date"),
            "name": str(row.get("name", "")),
            "listing_date": row.get("listing_date") or None,
            "expire_price": row.get("expire_price"),
            "update_date": row.get("update_date") or None,
        }
        for row in rows
    ]


def _map_futures_snapshot(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol_field(row, sym, "futures_id", Market.TW)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "open": _as_float(row.get("open")),
                "high": _as_float(row.get("high")),
                "low": _as_float(row.get("low")),
                "close": _as_float(row.get("close")),
                "change_price": _as_float(row.get("change_price")),
                "change_rate": _as_float(row.get("change_rate")),
                "average_price": _as_float(row.get("average_price")),
                "volume": _as_int(row.get("volume")),
                "total_volume": _as_int(row.get("total_volume")),
                "amount": _as_float(row.get("amount")),
                "total_amount": _as_float(row.get("total_amount")),
                "yesterday_volume": _as_int(row.get("yesterday_volume")),
                "buy_price": _as_float(row.get("buy_price")),
                "buy_volume": _as_int(row.get("buy_volume")),
                "sell_price": _as_float(row.get("sell_price")),
                "sell_volume": _as_int(row.get("sell_volume")),
                "volume_ratio": _as_float(row.get("volume_ratio")),
                "tick_type": str(row.get("TickType", "")),
            }
        )
    return rows_out


def _map_options_snapshot(
    rows: list[dict[str, object]], sym: Symbol | None
) -> list[dict[str, object]]:
    rows_out: list[dict[str, object]] = []
    for row in rows:
        row_sym = _row_symbol_field(row, sym, "options_id", Market.TW)
        rows_out.append(
            {
                "symbol": row_sym.id,
                "market": row_sym.market.value,
                "trade_date": _as_date_str(row["date"]),
                "open": _as_float(row.get("open")),
                "high": _as_float(row.get("high")),
                "low": _as_float(row.get("low")),
                "close": _as_float(row.get("close")),
                "change_price": _as_float(row.get("change_price")),
                "change_rate": _as_float(row.get("change_rate")),
                "average_price": _as_float(row.get("average_price")),
                "volume": _as_int(row.get("volume")),
                "total_volume": _as_int(row.get("total_volume")),
                "amount": _as_float(row.get("amount")),
                "total_amount": _as_float(row.get("total_amount")),
                "yesterday_volume": _as_int(row.get("yesterday_volume")),
                "buy_price": _as_float(row.get("buy_price")),
                "buy_volume": _as_int(row.get("buy_volume")),
                "sell_price": _as_float(row.get("sell_price")),
                "sell_volume": _as_int(row.get("sell_volume")),
                "volume_ratio": _as_float(row.get("volume_ratio")),
                "tick_type": str(row.get("TickType", "")),
            }
        )
    return rows_out


DATASETS: dict[Capability, _DatasetConfig] = {
    Capability.TICK_SNAPSHOT: _DatasetConfig(
        dataset="taiwan_stock_tick_snapshot",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.LIVE_ISH,
        caveat="Sponsor-tier bounded intraday snapshot — a single bounded observation, never a "
        "live tick stream. `data_id` accepts either a 4-digit stock id or a 3-digit index code "
        "(91 documented codes spanning broad-market/sector/themed/leverage indices).",
        row_key="rows",
        row_mapper=_map_tick_snapshot,
        # Confirmed live: this dataset is NOT in the generic `/data` endpoint's `dataset` enum
        # (422 "Input should be ...") — FinMind serves every Real-Time-category dataset off its
        # own dedicated path instead, same `special_endpoint` mechanism
        # `chip_institutional.py`'s `taiwan_stock_trading_daily_report` already uses.
        special_endpoint="/taiwan_stock_tick_snapshot",
    ),
    Capability.FUTOPT_TICK_INFO: _DatasetConfig(
        dataset="TaiwanFutOptTickInfo",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        caveat="Whole-roster futures/options real-time-quote contract reference table; a "
        "static list, not a per-date time series — no `data_id` param exists.",
        row_key="rows",
        row_mapper=_map_futopt_tick_info,
        whole_market_ok=True,
    ),
    Capability.FUTURES_SNAPSHOT: _DatasetConfig(
        dataset="taiwan_futures_snapshot",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.LIVE_ISH,
        caveat="Sponsor-tier bounded intraday futures snapshot — a single bounded observation, "
        "never a live tick stream.",
        row_key="rows",
        row_mapper=_map_futures_snapshot,
        # Same dedicated-endpoint quirk as `TICK_SNAPSHOT` above — confirmed live.
        special_endpoint="/taiwan_futures_snapshot",
    ),
    Capability.OPTIONS_SNAPSHOT: _DatasetConfig(
        dataset="taiwan_options_snapshot",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.LIVE_ISH,
        caveat="Sponsor-tier bounded intraday options snapshot — a single bounded observation, "
        "never a live tick stream.",
        row_key="rows",
        row_mapper=_map_options_snapshot,
        # Same dedicated-endpoint quirk as `TICK_SNAPSHOT` above — confirmed live.
        special_endpoint="/taiwan_options_snapshot",
    ),
}

__all__ = ["DATASETS"]
