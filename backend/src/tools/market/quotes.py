"""Latest price quote tools (`get_price_quote`, `get_live_price_quote`)."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import EODBar
from schemas.enums import Capability, DataClass, Market
from schemas.source import HealthStatus
from tools.base import BaseTool, ToolContext, parse_args
from tools.common import SymbolInput
from tools.market._common import RowList, UntypedDict


def _latest_bar(payload: dict[str, object]) -> EODBar | None:
    """`price_eod` payload is `{"bars": [...]}` (FinMind, possibly many rows) or `{"bar": {...}}`
    (TWSE OpenAPI, single latest snapshot) — normalize both shapes to "the latest bar"."""
    bars = payload.get("bars")
    if isinstance(bars, list) and bars:
        rows = cast(RowList, bars)
        return EODBar.model_validate(rows[-1])
    bar = payload.get("bar")
    if isinstance(bar, dict):
        return EODBar.model_validate(cast(UntypedDict, bar))
    return None


class GetPriceQuoteOutput(BaseModel):
    """The basic price-quote read (part of the immediate-core set)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    bar: EODBar | None = None


class GetPriceQuoteTool(BaseTool):
    """`get_price_quote` — wraps `Capability.PRICE_EOD` directly via the router (not the full
    multi-capability canonical build — this is the narrow "just the quote" read)."""

    name = "get_price_quote"
    description = "Get the latest available end-of-day price bar for one stock/ETF symbol."
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetPriceQuoteOutput
    card_type: str | None = "price_quote"
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        result = await self._router.fetch(
            Capability.PRICE_EOD, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        return GetPriceQuoteOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            bar=_latest_bar(result.payload),
        )


class LivePriceTick(BaseModel):
    """One decoded `PricingData` WebSocket message — mapped straight off yfinance's own
    snake_case field names (`MessageToDict(..., preserving_proto_field_name=True)`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    price: float | None = None
    change: float | None = None
    change_percent: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    day_volume: int | None = None
    open_price: float | None = None
    previous_close: float | None = None
    bid: float | None = None
    bid_size: int | None = None
    ask: float | None = None
    ask_size: int | None = None
    currency: str | None = None
    market_hours: int | None = None
    observed_at: str | None = None


class GetLivePriceQuoteOutput(BaseModel):
    """The real-time (WebSocket-streamed) price-quote read — US-only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    tick: LivePriceTick | None = None


class GetLivePriceQuoteTool(BaseTool):
    """`get_live_price_quote` — wraps `Capability.LIVE_PRICE_QUOTE` (yfinance WebSocket, real-
    time-ish, US-only). Validates `market` up front rather than letting the router's `supports()`
    gate silently degrade for a market this tool never claims — a non-US call returns
    `HealthStatus.UNAVAILABLE_TIER` without ever touching the router."""

    name = "get_live_price_quote"
    description = "Get a real-time (WebSocket-streamed) price tick for one US stock/ETF symbol."
    input_model: type[BaseModel] = SymbolInput
    output_model: type[BaseModel] = GetLivePriceQuoteOutput
    card_type: str | None = "live_price_quote"
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(SymbolInput, args)
        if typed.market is not Market.US:
            return GetLivePriceQuoteOutput(
                source_id="none",
                currentness_caveat=(
                    f"get_live_price_quote only serves Market.US today, not {typed.market.value}."
                ),
                health_status=HealthStatus.UNAVAILABLE_TIER,
                tick=None,
            )
        result = await self._router.fetch(
            Capability.LIVE_PRICE_QUOTE, typed.to_symbol(), tls_policy=ctx.tls_policy
        )
        raw_tick = result.payload.get("tick")
        tick = LivePriceTick.model_validate(raw_tick) if isinstance(raw_tick, dict) else None
        return GetLivePriceQuoteOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            tick=tick,
        )
