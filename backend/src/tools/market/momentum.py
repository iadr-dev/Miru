"""Price-momentum rankings."""

from __future__ import annotations

from datetime import date as date_
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ingest.store import ParquetStore
from schemas.enums import DataClass, Market
from tools.base import BaseTool, ToolContext, parse_args
from tools.market._common import (
    closes_by_symbol,
    latest_dataset_date,
    names_by_symbol_for,
    price_eod_dataset_for,
    real_calendar,
    unavailable_reason_for_window,
)

_MOMENTUM_TOP_N = 10


# Trading-day lookback per period — never a calendar-day offset (`TradingDayCalendar.
# shift_business_days`). "month" is 21 trading days (~1 calendar month at 5 sessions/week),
# the same convention `analytics/score.py`'s own trend-quality window uses.
_MOMENTUM_PERIOD_TRADING_DAYS: dict[str, int] = {"day": 1, "week": 5, "month": 21}


class MomentumPeriod(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class GetPriceMomentumInput(BaseModel):
    """A market + a lookback period + an optional trading day (the window's end, defaults to
    latest available) + a top-N cap — mirrors `GetIndexContributionInput`'s date/top_n shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    period: MomentumPeriod = Field(
        default=MomentumPeriod.WEEK,
        description="Lookback window: 'day' (1 trading day), 'week' (5 trading days), or "
        "'month' (21 trading days).",
    )
    trade_date: date_ | None = Field(
        default=None,
        description="End of the momentum window (ISO date). Omitted or null means the latest "
        "available trading day.",
    )
    top_n: int = Field(default=_MOMENTUM_TOP_N, ge=1, le=50)


class PriceMomentumRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    stock_name: str
    pct_change: float  # (close - period_ago_close) / period_ago_close x 100, a whole percent


class GetPriceMomentumOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    period: MomentumPeriod = MomentumPeriod.WEEK
    trade_date: date_ | None = None
    period_ago_trade_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    top_gainers: tuple[PriceMomentumRow, ...] = Field(default_factory=tuple)
    top_losers: tuple[PriceMomentumRow, ...] = Field(default_factory=tuple)


class GetPriceMomentumTool(BaseTool):
    """`get_price_momentum` — 本日/本週/本月強勢股/弱勢股: pure close-to-close percentage change
    over a caller-chosen trading-day lookback (1/5/21 trading days — never a calendar-day
    offset, `TradingDayCalendar.shift_business_days`), ranked independently from
    `get_score_leaderboard`'s multi-factor composite score. Where the leaderboard blends
    momentum/trend_quality/value_guard/quality/revenue_flow (see `analytics/score.py`), this
    tool is single-factor: raw price return only, the same "who moved the most over period X" a
    trader means by that phrase, not a multi-factor AI opinion. Reads the same already-ingested
    `PRICE_EOD_JOB`/`STOCK_INFO_JOB` Parquet partitions `GetAdvanceDeclineTool`/
    `GetSectorHeatmapTool` do — never a per-symbol live fan-out, same whole-market-aggregate
    reasoning as those tools' own docstrings.

    Public tier: both `Capability.PRICE_EOD` and `Capability.STOCK_INFO` are `DataClass.PUBLIC`
    (`sources/finmind/technical.py`), the same tier reasoning `GetAdvanceDeclineTool` gives."""

    name = "get_price_momentum"
    description = (
        "Get the whole-market top-N gainers/losers by pure close-to-close percentage change "
        "over a chosen lookback period (day/week/month, defaults to week, ending at the "
        "latest available day) — a single-factor price-momentum ranking, distinct from "
        "get_score_leaderboard's multi-factor composite score."
    )
    input_model: type[BaseModel] = GetPriceMomentumInput
    output_model: type[BaseModel] = GetPriceMomentumOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "price_momentum"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetPriceMomentumInput, args)
        market = typed.market.value
        price_eod_dataset = price_eod_dataset_for(market)
        calendar = real_calendar(self._store, market)
        trade_date = typed.trade_date or latest_dataset_date(self._store, price_eod_dataset, market)
        if trade_date is None or not self._store.has_partition(
            price_eod_dataset, market, trade_date
        ):
            return GetPriceMomentumOutput(
                market=market,
                period=typed.period,
                trade_date=typed.trade_date,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "price_eod", market, typed.trade_date
                ),
            )
        period_ago_date = calendar.shift_business_days(
            trade_date, -_MOMENTUM_PERIOD_TRADING_DAYS[typed.period.value]
        )
        if not self._store.has_partition(price_eod_dataset, market, period_ago_date):
            return GetPriceMomentumOutput(
                market=market,
                period=typed.period,
                trade_date=trade_date,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window("price_eod", market, trade_date),
            )
        names_by_symbol = names_by_symbol_for(self._store, market)
        if not names_by_symbol:
            return GetPriceMomentumOutput(
                market=market,
                period=typed.period,
                trade_date=trade_date,
                period_ago_trade_date=period_ago_date,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window("stock_info", market, trade_date),
            )

        today_closes = closes_by_symbol(self._store, market, trade_date)
        period_ago_closes = closes_by_symbol(self._store, market, period_ago_date)

        rows: list[PriceMomentumRow] = []
        for symbol, close in today_closes.items():
            period_ago_close = period_ago_closes.get(symbol)
            name = names_by_symbol.get(symbol)
            if not period_ago_close or not name:
                continue
            pct = (close - period_ago_close) / period_ago_close * 100
            rows.append(PriceMomentumRow(symbol=symbol, stock_name=name, pct_change=pct))

        ranked = sorted(rows, key=lambda r: r.pct_change, reverse=True)
        top_gainers = tuple(ranked[: typed.top_n])
        top_losers = tuple(sorted(ranked, key=lambda r: r.pct_change)[: typed.top_n])

        return GetPriceMomentumOutput(
            market=market,
            period=typed.period,
            trade_date=trade_date,
            period_ago_trade_date=period_ago_date,
            data_available=True,
            top_gainers=top_gainers,
            top_losers=top_losers,
        )
