"""International-markets tools — FinMind's own `us_stock_info`/`us_stock_price`/... family
(distinct capability names from the shared `price_eod`/`sector_class` capabilities yfinance
serves as primary for non-TW markets — see `config/source_priority.yaml`'s international-
markets note). Bundled into one info+price tool per market rather than eight near-duplicate
tools, since a caller almost always wants "roster info" and "price" together for a given
non-TW market and both datasets share the same `id` (the market's own quote symbol, e.g.
`^DJI`/`BP.L`/`AALB.AS`/`7203.T`).

`us_stock_price_minute` (Sponsor-tier, US-only) is wrapped separately since it has no
counterpart in the other three markets.
"""

from __future__ import annotations

from datetime import date as date_
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import (
    InternationalStockInfoRow,
    InternationalStockPriceRow,
    UsStockPriceMinuteRow,
)
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from schemas.source import HealthStatus
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import DateRangeInput


class FinMindInternationalMarket(StrEnum):
    """The four non-TW markets FinMind carries its own roster+price datasets for — distinct
    from the full `schemas.enums.Market` set (HK/GLOBAL have no FinMind international dataset)."""

    US = "US"
    GB = "GB"
    EU = "EU"
    JP = "JP"


_INFO_CAPABILITY: dict[FinMindInternationalMarket, Capability] = {
    FinMindInternationalMarket.US: Capability.US_STOCK_INFO,
    FinMindInternationalMarket.GB: Capability.UK_STOCK_INFO,
    FinMindInternationalMarket.EU: Capability.EUROPE_STOCK_INFO,
    FinMindInternationalMarket.JP: Capability.JAPAN_STOCK_INFO,
}
_PRICE_CAPABILITY: dict[FinMindInternationalMarket, Capability] = {
    FinMindInternationalMarket.US: Capability.US_STOCK_PRICE,
    FinMindInternationalMarket.GB: Capability.UK_STOCK_PRICE,
    FinMindInternationalMarket.EU: Capability.EUROPE_STOCK_PRICE,
    FinMindInternationalMarket.JP: Capability.JAPAN_STOCK_PRICE,
}
_TO_MARKET: dict[FinMindInternationalMarket, Market] = {
    FinMindInternationalMarket.US: Market.US,
    FinMindInternationalMarket.GB: Market.GB,
    FinMindInternationalMarket.EU: Market.EU,
    FinMindInternationalMarket.JP: Market.JP,
}


class GetInternationalStockInput(BaseModel):
    """`id` is the market's own quote symbol (e.g. `^DJI`/`BP.L`/`AALB.AS`/`7203.T`), never a
    Taiwan-style numeric id. `market` selects which FinMind international dataset family to
    use — a distinct, narrower enum from `SymbolInput`'s full `Market` (FinMind only carries
    this family for four non-TW markets), so this input does not subclass `SymbolInput`/
    `DateRangeInput`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, description="The market's own quote symbol, e.g. '^DJI'.")
    market: FinMindInternationalMarket
    start_date: date_ | None = None
    end_date: date_ | None = None

    def to_symbol(self) -> Symbol:
        return Symbol(id=self.id, market=_TO_MARKET[self.market])

    def date_params(self) -> dict[str, object]:
        params: dict[str, object] = {}
        if self.start_date is not None:
            params["start_date"] = self.start_date.isoformat()
        if self.end_date is not None:
            params["end_date"] = self.end_date.isoformat()
        return params


class GetInternationalStockOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currentness_caveat: str = Field(min_length=1)
    info: tuple[InternationalStockInfoRow, ...] = Field(default_factory=tuple)
    price: tuple[InternationalStockPriceRow, ...] = Field(default_factory=tuple)


class GetInternationalStockTool(BaseTool):
    """`get_international_stock` — bundles FinMind's roster-info + EOD-price datasets for one
    non-TW market."""

    name = "get_international_stock"
    description = (
        "Get FinMind's own roster-info + EOD-price rows for one US/GB/EU/JP-market symbol "
        "(distinct from the yfinance-primary price_eod path for the same markets)."
    )
    input_model: type[BaseModel] = GetInternationalStockInput
    output_model: type[BaseModel] = GetInternationalStockOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "international_stock"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetInternationalStockInput, args)
        symbol = typed.to_symbol()
        params = typed.date_params()
        info_result, info_rows = await fetch_rows(
            self._router,
            _INFO_CAPABILITY[typed.market],
            symbol,
            tls_policy=ctx.tls_policy,
            model=InternationalStockInfoRow,
            key="rows",
        )
        _price_result, price_rows = await fetch_rows(
            self._router,
            _PRICE_CAPABILITY[typed.market],
            symbol,
            tls_policy=ctx.tls_policy,
            model=InternationalStockPriceRow,
            key="rows",
            params=params,
        )
        return GetInternationalStockOutput(
            currentness_caveat=info_result.currentness_caveat,
            info=info_rows,
            price=price_rows,
        )


class GetUsStockPriceMinuteOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    rows: tuple[UsStockPriceMinuteRow, ...] = Field(default_factory=tuple)


class GetUsStockPriceMinuteTool(BaseTool):
    """`get_us_stock_price_minute` — wraps `Capability.US_STOCK_PRICE_MINUTE` (Sponsor-tier)."""

    name = "get_us_stock_price_minute"
    description = "Get FinMind's US-market minute-bar series for one symbol (Sponsor-tier)."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetUsStockPriceMinuteOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "us_price_minute"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.US_STOCK_PRICE_MINUTE,
            Symbol(id=typed.id, market=Market.US),
            tls_policy=ctx.tls_policy,
            model=UsStockPriceMinuteRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetUsStockPriceMinuteOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [
        (GetInternationalStockTool(router), False),
        (GetUsStockPriceMinuteTool(router), False),
    ]


__all__ = [
    "FinMindInternationalMarket",
    "GetInternationalStockInput",
    "GetInternationalStockOutput",
    "GetInternationalStockTool",
    "GetUsStockPriceMinuteOutput",
    "GetUsStockPriceMinuteTool",
    "build_registration_table",
]
