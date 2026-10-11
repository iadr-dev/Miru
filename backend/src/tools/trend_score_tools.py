"""`get_trend_score` — the stock-detail-page read for the 趨勢評分 (trend score) composite.

Five of the six dimensions (`technical`/`chip_flow`/`margin_credit`/`sector_rotation`/
`taiex_correlation`) are cache-only, exactly like `score_tools.py`'s `get_stock_score`: the daily
`job_trend_score_batch` already computed them for the whole universe, so this tool reads
`trend_scores` back rather than recomputing — see `analytics/scoring_batch.py`'s own
`job_trend_score_batch` docstring for why a live per-symbol recompute of those five would either
duplicate the whole-universe read or rank against a stale/partial universe.

The sixth dimension, `broker_concentration`, is deliberately NEVER batch-computed — `chip_broker`
(分點) has no whole-market request form (Sponsor-tier, per-symbol/per-broker fetch only; see
`ingest/jobs.py`'s `STUBBED_DATASETS` comment). This tool computes it on demand, for just the one
requested symbol, via a live `Capability.CHIP_BROKER` fetch (mirroring
`tools/market/chip_flows.py::GetChipFlowsTool`'s own fetch shape, capped at the same
`_CHIP_BROKER_MAX_TRADING_DAYS` window) and re-blends the full six-dimension composite at request
time — the one dimension in this tool that is genuinely live, not a cache read. An unentitled
(non-Sponsor) caller gets the batch's five-dimension composite with `broker_concentration`
disclosed as locked, never a live Sponsor-tier fetch on their behalf.

`data_class = DataClass.PUBLIC`: the five batch-computed dimensions are all built from
already-PUBLIC-tier datasets (`price_eod`/`institutional_flow`/`margin_short` — see
`job_trend_score_batch`'s own docstring), so the composite itself is public; only the one
`broker_concentration` sub-dimension needs the Sponsor entitlement check, applied inline
(`is_entitled`) rather than via `dispatch_tool`'s own `data_class=SPONSOR` substitution, since the
tool's overall output is real and useful even for an unentitled caller — the same "aggregate
stays real, one sub-part locks" split `GetChipFlowsTool` already uses for its own row-level lock.
"""

from __future__ import annotations

from datetime import date as date_
from datetime import timedelta
from typing import cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.models import TrendDimensionResult
from analytics.trend_score import (
    DIMENSION_WEIGHTS,
    US_DIMENSION_WEIGHTS,
    TrendDimension,
    blend_trend_composite,
    broker_concentration_trend_score,
)
from analytics.trend_score_store import get_cached_trend_score, get_trend_score_history
from dataaccess.router import DataAccessRouter
from schemas.capabilities import ChipBrokerRow, EODBar
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, is_entitled, parse_args
from tools.common import candidate_trading_days

_DEFAULT_HISTORY_DAYS = 90
_MAX_HISTORY_DAYS = 250
_BROKER_CONCENTRATION_LOOKBACK_TRADING_DAYS = 10  # matches GetChipFlowsTool's own fetch window
_LOOKBACK_CALENDAR_DAYS = 20  # generous calendar-day span to find that many real trading days


class GetTrendScoreInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, description="Exchange-scoped symbol id, e.g. '2330'.")
    market: Market = Field(description="Exchange market the symbol id is scoped to, e.g. 'TW'.")
    days: int = Field(
        default=_DEFAULT_HISTORY_DAYS,
        ge=1,
        le=_MAX_HISTORY_DAYS,
        description="How many trailing calendar days of trend-score history to include for the "
        "chart (bounded, never an open-ended range).",
    )


class TrendScoreDimensionOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: float | None = Field(default=None, ge=0.0, le=100.0)
    description: str
    unavailable_reason: str | None = None
    locked: bool = False


class TrendScoreHistoryPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trade_date: date_
    composite: float | None = Field(default=None, ge=0.0, le=100.0)
    close: float | None = None


class GetTrendScoreOutput(BaseModel):
    """`found=False` (with `message` explaining why) when the batch has not yet computed today's
    five cache-backed dimensions for this symbol — never a fabricated composite. `history` is
    always populated from whatever cached rows exist in the requested window, independent of
    whether *today's* row exists yet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    found: bool
    symbol: str
    market: Market
    as_of: date_ | None = None
    composite: float | None = Field(default=None, ge=0.0, le=100.0)
    dimensions: tuple[TrendScoreDimensionOutput, ...] = Field(default_factory=tuple)
    unavailable_dimensions: tuple[str, ...] = Field(default=())
    history: tuple[TrendScoreHistoryPoint, ...] = Field(default_factory=tuple)
    non_trading_caveat: str = ""
    message: str = ""


def _dimension_result_to_dimension(result: TrendDimensionResult) -> TrendDimension:
    """`TrendDimensionResult` (frozen pydantic, `trend_score_store.py`'s own read shape) back to
    the plain `TrendDimension` dataclass `blend_trend_composite` operates on — this tool is the
    one place a cached row's five dimensions and a freshly-computed sixth are blended together,
    so it needs both shapes to interoperate."""
    return TrendDimension(
        name=result.name,
        value=result.value,
        description=result.description,
        unavailable_reason=result.unavailable_reason,
    )


async def _fetch_broker_concentration(
    router: DataAccessRouter, *, symbol: str, market: Market, ctx: ToolContext
) -> TrendDimension:
    """Live `Capability.CHIP_BROKER` fetch for just this one symbol, capped at the same trailing
    window `GetChipFlowsTool` uses — the one genuinely on-demand (not cache-read) piece of this
    tool. Returns a disclosed-unavailable dimension (never raises) if the fetch comes back empty,
    matching `trend_score.py`'s own "never fabricate" discipline."""

    sym = Symbol(id=symbol, market=market)
    end = date_.today()
    start = end - timedelta(days=_LOOKBACK_CALENDAR_DAYS)
    days = candidate_trading_days(
        start, end, limit=_BROKER_CONCENTRATION_LOOKBACK_TRADING_DAYS
    ) or (end,)

    rows: list[ChipBrokerRow] = []
    for day in days:
        result = await router.fetch(
            Capability.CHIP_BROKER, sym, tls_policy=ctx.tls_policy, date=day.isoformat()
        )
        raw_rows = result.payload.get("rows")
        if isinstance(raw_rows, list):
            rows.extend(
                ChipBrokerRow.model_validate(row)
                for row in cast("list[dict[str, object]]", raw_rows)
            )

    net_by_broker: dict[str, int] = {}
    for row in rows:
        net_by_broker[row.broker_id] = net_by_broker.get(row.broker_id, 0) + row.buy - row.sell
    overall_net = sum(net_by_broker.values())
    top_by_magnitude = sorted(net_by_broker.values(), key=abs, reverse=True)[:10]
    main_force_net = sum(top_by_magnitude)
    return broker_concentration_trend_score(
        rows, main_force_net=main_force_net, overall_net=overall_net
    )


async def _closes_by_date(  # noqa: PLR0913 — one keyword-only field per fetch-scoping dimension
    router: DataAccessRouter,
    *,
    symbol: str,
    market: Market,
    start_date: date_,
    end_date: date_,
    ctx: ToolContext,
) -> dict[date_, float]:
    """The real closing price for each trading day in `[start_date, end_date]` — the chart's
    dual-axis price overlay (`TrendScoreHistoryPoint.close`), one `Capability.PRICE_EOD` range
    fetch, never a second per-day call. Returns an empty dict (never fabricated closes) if the
    fetch comes back with no rows — the caller leaves `close=None` for every point in that case."""
    sym = Symbol(id=symbol, market=market)
    _result, rows = await fetch_rows(
        router,
        Capability.PRICE_EOD,
        sym,
        tls_policy=ctx.tls_policy,
        model=EODBar,
        key="bars",
        params={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
    )
    return {row.trade_date: row.close for row in rows}


class GetTrendScoreTool(BaseTool):
    """`get_trend_score` — the six-dimension 趨勢評分 composite for one symbol: five dimensions
    read from the daily batch's cache, `broker_concentration` computed live for just this symbol
    and blended in at request time. See the module docstring for the full design."""

    name = "get_trend_score"
    description = (
        "Get one stock/ETF's 趨勢評分 (trend score, 0-100): a six-dimension descriptive read of "
        "trend strength combining technical indicators, institutional chip flow, margin/credit "
        "balance trend, broker-branch concentration, sector-rotation standing, and TAIEX "
        "co-movement — plus trailing history for a chart. Descriptive only, never a forecast or "
        "a buy/sell/hold signal."
    )
    input_model: type[BaseModel] = GetTrendScoreInput
    output_model: type[BaseModel] = GetTrendScoreOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "trend_score"

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        router: DataAccessRouter,
    ) -> None:
        self._session_factory = session_factory
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetTrendScoreInput, args)
        market = typed.market

        end_date = date_.today()
        start_date = end_date - timedelta(days=typed.days)

        async with self._session_factory() as session:
            cached = await get_cached_trend_score(session, symbol=typed.id, market=market)
            history_rows = await get_trend_score_history(
                session, symbol=typed.id, market=market, start_date=start_date, end_date=end_date
            )

        closes_by_date = await _closes_by_date(
            self._router,
            symbol=typed.id,
            market=market,
            start_date=start_date,
            end_date=end_date,
            ctx=ctx,
        )
        history = tuple(
            TrendScoreHistoryPoint(
                trade_date=row.as_of,
                composite=row.composite,
                close=closes_by_date.get(row.as_of),
            )
            for row in history_rows
        )

        if cached is None:
            return GetTrendScoreOutput(
                found=False,
                symbol=typed.id,
                market=market,
                history=history,
                message=(
                    f"No trend score has been computed yet for {typed.id} ({market.value}) — "
                    "the daily trend-score batch has not yet run or this symbol has no ingested "
                    "data."
                ),
            )

        entitled = is_entitled(DataClass.SPONSOR, ctx.tier)
        cached_dimensions = [_dimension_result_to_dimension(d) for d in cached.dimensions]
        by_name = {d.name: d for d in cached_dimensions}
        # `broker_concentration` (分點, `Capability.CHIP_BROKER`) is TW-only — no US equivalent
        # exists anywhere in this codebase (see `analytics/trend_score.py::US_DIMENSION_WEIGHTS`'s
        # own comment for the four US-native dimensions that replace TW's chip/margin/broker/
        # sector-rotation set). Only a TW row ever ships that placeholder to begin with, so the
        # live on-demand fetch/replace only ever applies there.
        weights = DIMENSION_WEIGHTS if market is Market.TW else US_DIMENSION_WEIGHTS
        if entitled and market is Market.TW:
            live_concentration = await _fetch_broker_concentration(
                self._router, symbol=typed.id, market=market, ctx=ctx
            )
            by_name["broker_concentration"] = live_concentration
        dimensions_in_order = [by_name[name] for name in weights if name in by_name]
        composite, unavailable = blend_trend_composite(dimensions_in_order, weights=weights)

        dimension_outputs = tuple(
            TrendScoreDimensionOutput(
                name=d.name,
                value=d.value,
                description=d.description,
                unavailable_reason=d.unavailable_reason,
                locked=d.name == "broker_concentration" and not entitled,
            )
            for d in dimensions_in_order
        )

        return GetTrendScoreOutput(
            found=True,
            symbol=typed.id,
            market=market,
            as_of=cached.as_of,
            composite=composite,
            dimensions=dimension_outputs,
            unavailable_dimensions=unavailable,
            history=history,
            non_trading_caveat=cached.non_trading_caveat,
        )


def build_registration_table(
    session_factory: async_sessionmaker[AsyncSession],
    router: DataAccessRouter,
) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table, parameterized by both the DB session
    factory (score/trend caches) and the router (the live `broker_concentration` fetch) — matching
    `score_tools.py`'s pattern but needing both seams rather than just one."""
    return [(GetTrendScoreTool(session_factory, router), False)]


__all__ = [
    "GetTrendScoreInput",
    "GetTrendScoreOutput",
    "GetTrendScoreTool",
    "build_registration_table",
]
