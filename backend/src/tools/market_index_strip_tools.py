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

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del args
        rows: list[MarketIndexStripItemRow] = []

        _taiex_result, taiex_rows = await fetch_rows(
            self._router,
            Capability.TAIEX_5S_INDEX,
            Symbol(id="", market=Market.TW),
            tls_policy=ctx.tls_policy,
            model=Taiex5sIndexRow,
            key="rows",
        )
        if taiex_rows:
            latest_taiex = taiex_rows[-1]
            prior_taiex = taiex_rows[-2].taiex if len(taiex_rows) > 1 else None
            change, change_pct = _change(latest_taiex.taiex, prior_taiex)
            rows.append(
                MarketIndexStripItemRow(
                    key="taiex",
                    label="加權指數",
                    value=latest_taiex.taiex,
                    change=change,
                    change_pct=change_pct,
                    as_of=latest_taiex.trade_date,
                )
            )

        _otc_result, otc_rows = await fetch_rows(
            self._router,
            Capability.TOTAL_RETURN_INDEX,
            Symbol(id="TPEx", market=Market.TW),
            tls_policy=ctx.tls_policy,
            model=TotalReturnIndexRow,
            key="rows",
        )
        if otc_rows:
            latest_otc = otc_rows[-1]
            prior_otc = otc_rows[-2].price if len(otc_rows) > 1 else None
            change, change_pct = _change(latest_otc.price, prior_otc)
            rows.append(
                MarketIndexStripItemRow(
                    key="otc",
                    label="櫃買指數",
                    value=latest_otc.price,
                    change=change,
                    change_pct=change_pct,
                    as_of=latest_otc.trade_date,
                )
            )

        _fx_result, fx_rows = await fetch_rows(
            self._router,
            Capability.EXCHANGE_RATE,
            Symbol(id="USD", market=Market.TW),
            tls_policy=ctx.tls_policy,
            model=ExchangeRateRow,
            key="rows",
        )
        if fx_rows:
            latest_fx = fx_rows[-1]
            prior_fx = fx_rows[-2].spot_buy if len(fx_rows) > 1 else None
            change, change_pct = _change(latest_fx.spot_buy, prior_fx)
            rows.append(
                MarketIndexStripItemRow(
                    key="usd_twd",
                    label="美元/台幣",
                    value=latest_fx.spot_buy,
                    change=change,
                    change_pct=change_pct,
                    as_of=latest_fx.trade_date,
                )
            )

        _jpy_result, jpy_rows = await fetch_rows(
            self._router,
            Capability.EXCHANGE_RATE,
            Symbol(id="JPY", market=Market.TW),
            tls_policy=ctx.tls_policy,
            model=ExchangeRateRow,
            key="rows",
        )
        if jpy_rows:
            latest_jpy = jpy_rows[-1]
            prior_jpy = jpy_rows[-2].spot_buy if len(jpy_rows) > 1 else None
            change, change_pct = _change(latest_jpy.spot_buy, prior_jpy)
            rows.append(
                MarketIndexStripItemRow(
                    key="jpy_twd",
                    label="日圓/台幣",
                    value=latest_jpy.spot_buy,
                    change=change,
                    change_pct=change_pct,
                    as_of=latest_jpy.trade_date,
                )
            )

        _gold_result, gold_rows = await fetch_rows(
            self._router,
            Capability.GOLD_PRICE,
            Symbol(id="", market=Market.GLOBAL),
            tls_policy=ctx.tls_policy,
            model=GoldPriceRow,
            key="rows",
        )
        if gold_rows:
            latest_gold = gold_rows[-1]
            prior_gold = gold_rows[-2].price if len(gold_rows) > 1 else None
            change, change_pct = _change(latest_gold.price, prior_gold)
            rows.append(
                MarketIndexStripItemRow(
                    key="gold",
                    label="黃金",
                    value=latest_gold.price,
                    change=change,
                    change_pct=change_pct,
                    as_of=latest_gold.date,
                )
            )

        _wti_result, wti_rows = await fetch_rows(
            self._router,
            Capability.CRUDE_OIL_PRICES,
            Symbol(id="WTI", market=Market.GLOBAL),
            tls_policy=ctx.tls_policy,
            model=CrudeOilPricesRow,
            key="rows",
        )
        if wti_rows:
            latest_wti = wti_rows[-1]
            prior_wti = wti_rows[-2].price if len(wti_rows) > 1 else None
            change, change_pct = _change(latest_wti.price, prior_wti)
            rows.append(
                MarketIndexStripItemRow(
                    key="crude_oil_wti",
                    label="原油 WTI",
                    value=latest_wti.price,
                    change=change,
                    change_pct=change_pct,
                    as_of=latest_wti.date,
                )
            )

        _brent_result, brent_rows = await fetch_rows(
            self._router,
            Capability.CRUDE_OIL_PRICES,
            Symbol(id="Brent", market=Market.GLOBAL),
            tls_policy=ctx.tls_policy,
            model=CrudeOilPricesRow,
            key="rows",
        )
        if brent_rows:
            latest_brent = brent_rows[-1]
            prior_brent = brent_rows[-2].price if len(brent_rows) > 1 else None
            change, change_pct = _change(latest_brent.price, prior_brent)
            rows.append(
                MarketIndexStripItemRow(
                    key="crude_oil_brent",
                    label="原油 Brent",
                    value=latest_brent.price,
                    change=change,
                    change_pct=change_pct,
                    as_of=latest_brent.date,
                )
            )

        return GetMarketIndexStripOutput(rows=tuple(rows))


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [(GetMarketIndexStripTool(router), False)]


__all__ = [
    "GetMarketIndexStripInput",
    "GetMarketIndexStripOutput",
    "GetMarketIndexStripTool",
    "build_registration_table",
]
