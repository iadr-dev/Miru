"""The TW-tab ticker-strip aggregator tool — 加權指數/櫃買指數/美元/日圓/黃金/WTI/Brent 原油 in one
public bundle.

Every underlying capability here (`TAIEX_5S_INDEX`/`TOTAL_RETURN_INDEX`/`EXCHANGE_RATE`/
`GOLD_PRICE`/`CRUDE_OIL_PRICES`) is already `DataClass.PUBLIC` at its own dataset declaration
(`sources/finmind/technical.py`, `sources/finmind/global_economic.py`) — this tool never re-tiers
a Sponsor-gated capability. The strip's other items (半導體業 index/台指期/VIX) stay on their own
existing Sponsor tools (`get_tick_snapshot`/`get_futures_snapshot`/`get_taiex_vix`) and render
masked for an unentitled caller — this bundle intentionally never reaches for those.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import (
    CrudeOilPricesRow,
    ExchangeRateRow,
    GoldPriceRow,
    MarketIndexStripItemRow,
    Taiex5sIndexRow,
    TotalReturnIndexRow,
)
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext


class GetMarketIndexStripInput(BaseModel):
    """No fields — every underlying dataset here is a market-level panel with no per-symbol or
    per-market scope a caller could narrow (mirrors `GetMacroIndicatorsInput`'s own reasoning)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class GetMarketIndexStripOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: tuple[MarketIndexStripItemRow, ...] = Field(default_factory=tuple)


def _change(latest: float, prior: float | None) -> tuple[float | None, float | None]:
    """`(change, change_pct)` against `prior`, or `(None, None)` when there is no prior
    reading to diff against — never a fabricated zero."""
    if prior is None:
        return None, None
    change = latest - prior
    change_pct = (change / prior * 100) if prior != 0 else None
    return change, change_pct


@dataclass(frozen=True)
class _StripSpec[RowT: BaseModel]:
    """One strip item: which capability/symbol to fetch, how to read its value and date."""

    key: str
    label: str
    cap: Capability
    symbol: Symbol
    model: type[RowT]
    value_of: Callable[[RowT], float]
    as_of_of: Callable[[RowT], date]


_TAIEX = _StripSpec(
    "taiex",
    "加權指數",
    Capability.TAIEX_5S_INDEX,
    Symbol(id="", market=Market.TW),
    Taiex5sIndexRow,
    lambda r: r.taiex,
    lambda r: r.trade_date,
)
_OTC = _StripSpec(
    "otc",
    "櫃買指數",
    Capability.TOTAL_RETURN_INDEX,
    Symbol(id="TPEx", market=Market.TW),
    TotalReturnIndexRow,
    lambda r: r.price,
    lambda r: r.trade_date,
)
_USD = _StripSpec(
    "usd_twd",
    "美元/台幣",
    Capability.EXCHANGE_RATE,
    Symbol(id="USD", market=Market.TW),
    ExchangeRateRow,
    lambda r: r.spot_buy,
    lambda r: r.trade_date,
)
_JPY = _StripSpec(
    "jpy_twd",
    "日圓/台幣",
    Capability.EXCHANGE_RATE,
    Symbol(id="JPY", market=Market.TW),
    ExchangeRateRow,
    lambda r: r.spot_buy,
    lambda r: r.trade_date,
)
_GOLD = _StripSpec(
    "gold",
    "黃金",
    Capability.GOLD_PRICE,
    Symbol(id="", market=Market.GLOBAL),
    GoldPriceRow,
    lambda r: r.price,
    lambda r: r.date,
)
_WTI = _StripSpec(
    "crude_oil_wti",
    "原油 WTI",
    Capability.CRUDE_OIL_PRICES,
    Symbol(id="WTI", market=Market.GLOBAL),
    CrudeOilPricesRow,
    lambda r: r.price,
    lambda r: r.date,
)
_BRENT = _StripSpec(
    "crude_oil_brent",
    "原油 Brent",
    Capability.CRUDE_OIL_PRICES,
    Symbol(id="Brent", market=Market.GLOBAL),
    CrudeOilPricesRow,
    lambda r: r.price,
    lambda r: r.date,
)


class GetMarketIndexStripTool(BaseTool):
    """`get_market_index_strip` — bundles 加權指數(TAIEX_5S_INDEX)/櫃買指數(TOTAL_RETURN_INDEX
    id=TPEx)/美元兌台幣+日圓兌台幣(EXCHANGE_RATE id=USD/JPY)/黃金(GOLD_PRICE)/WTI+Brent 原油
    (CRUDE_OIL_PRICES id=WTI/Brent) into one public ticker-strip report — 7 items total. Each
    item is fetched independently — a missing/short row list for one item simply omits that item
    from `rows` rather than fabricating a value, so a single upstream hiccup never blanks the
    whole strip."""

    name = "get_market_index_strip"
    description = (
        "Get the TW-tab ticker-strip items (加權指數/櫃買指數/美元/日圓/黃金/WTI/Brent 原油) — "
        "current value plus change vs. the prior available reading, for each item independently."
    )
    input_model: type[BaseModel] = GetMarketIndexStripInput
    output_model: type[BaseModel] = GetMarketIndexStripOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "market_index_strip"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def _item[RowT: BaseModel](
        self, ctx: ToolContext, spec: _StripSpec[RowT]
    ) -> MarketIndexStripItemRow | None:
        """One item from its latest row vs. the one before, or `None` when it has no rows."""
        _result, item_rows = await fetch_rows(
            self._router,
            spec.cap,
            spec.symbol,
            tls_policy=ctx.tls_policy,
            model=spec.model,
            key="rows",
        )
        if not item_rows:
            return None
        latest = item_rows[-1]
        value = spec.value_of(latest)
        prior = spec.value_of(item_rows[-2]) if len(item_rows) > 1 else None
        change, change_pct = _change(value, prior)
        return MarketIndexStripItemRow(
            key=spec.key,
            label=spec.label,
            value=value,
            change=change,
            change_pct=change_pct,
            as_of=spec.as_of_of(latest),
        )

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del args
        items = [
            await self._item(ctx, _TAIEX),
            await self._item(ctx, _OTC),
            await self._item(ctx, _USD),
            await self._item(ctx, _JPY),
            await self._item(ctx, _GOLD),
            await self._item(ctx, _WTI),
            await self._item(ctx, _BRENT),
        ]
        return GetMarketIndexStripOutput(rows=tuple(item for item in items if item is not None))


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [(GetMarketIndexStripTool(router), False)]


__all__ = [
    "GetMarketIndexStripInput",
    "GetMarketIndexStripOutput",
    "GetMarketIndexStripTool",
    "build_registration_table",
]
