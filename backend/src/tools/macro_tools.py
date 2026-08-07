"""Macro/global-economic tools — the FinMind Global-Economic-Data-category datasets plus the
TW-scoped business-climate indicator. `get_macro_indicators` bundles exchange-rate/central-
bank-rate/gold/crude-oil/government-bond-yield/fear-greed into one report, since these are all
small, market-level, no-symbol-scope panels a caller almost always wants together as "macro
context," not one at a time.
"""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import (
    BusinessIndicatorRow,
    CentralBankRateRow,
    CrudeOilPricesRow,
    ExchangeRateRow,
    FearGreedIndexRow,
    GoldPriceRow,
    GovernmentBondYieldRow,
)
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import MarketOnlyInput


class _RowsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currentness_caveat: str = Field(min_length=1)


class GetMacroIndicatorsInput(BaseModel):
    """No per-symbol scope — every underlying dataset here is a market-level panel. `market`
    is accepted for interface symmetry with every other tool but is not itself forwarded to the
    router (each sub-fetch uses the `Market` its own dataset is actually modeled under —
    `exchange_rate` is TW-scoped, `gold_price`/`crude_oil_prices`/`central_bank_rate` are
    GLOBAL, `government_bond_yield`/`fear_greed_index` are US — see `schemas/capabilities.py`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_date: date_ | None = None
    end_date: date_ | None = None

    def date_params(self) -> dict[str, object]:
        params: dict[str, object] = {}
        if self.start_date is not None:
            params["start_date"] = self.start_date.isoformat()
        if self.end_date is not None:
            params["end_date"] = self.end_date.isoformat()
        return params


class GetMacroIndicatorsOutput(_RowsOutput):
    exchange_rate: tuple[ExchangeRateRow, ...] = Field(default_factory=tuple)
    central_bank_rate: tuple[CentralBankRateRow, ...] = Field(default_factory=tuple)
    gold_price: tuple[GoldPriceRow, ...] = Field(default_factory=tuple)
    crude_oil_prices: tuple[CrudeOilPricesRow, ...] = Field(default_factory=tuple)
    government_bond_yield: tuple[GovernmentBondYieldRow, ...] = Field(default_factory=tuple)
    fear_greed_index: tuple[FearGreedIndexRow, ...] = Field(default_factory=tuple)


class GetMacroIndicatorsTool(BaseTool):
    """`get_macro_indicators` — bundles 匯率/利率/黃金/原油/美債殖利率/Fear-Greed into one macro
    context report. `fear_greed_index` is Sponsor-tier; every other field is public — the whole
    tool is gated Sponsor so the entitlement check runs once at the tool level rather than
    masking one field mid-payload (a possible future per-factor masking scheme for a composite
    score tool is a different design this bundle does not attempt to replicate)."""

    name = "get_macro_indicators"
    description = (
        "Get exchange-rate, central-bank policy-rate, gold, crude-oil, US Treasury yield, and "
        "Fear & Greed Index readings in one macro-context report."
    )
    input_model: type[BaseModel] = GetMacroIndicatorsInput
    output_model: type[BaseModel] = GetMacroIndicatorsOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "macro_indicators"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetMacroIndicatorsInput, args)
        params = typed.date_params()

        fx_result, fx_rows = await fetch_rows(
            self._router,
            Capability.EXCHANGE_RATE,
            Symbol(id="", market=Market.TW),
            tls_policy=ctx.tls_policy,
            model=ExchangeRateRow,
            key="rows",
            params=params,
        )
        _rate_result, rate_rows = await fetch_rows(
            self._router,
            Capability.CENTRAL_BANK_RATE,
            Symbol(id="", market=Market.GLOBAL),
            tls_policy=ctx.tls_policy,
            model=CentralBankRateRow,
            key="rows",
            params=params,
        )
        _gold_result, gold_rows = await fetch_rows(
            self._router,
            Capability.GOLD_PRICE,
            Symbol(id="", market=Market.GLOBAL),
            tls_policy=ctx.tls_policy,
            model=GoldPriceRow,
            key="rows",
            params=params,
        )
        _oil_result, oil_rows = await fetch_rows(
            self._router,
            Capability.CRUDE_OIL_PRICES,
            Symbol(id="", market=Market.GLOBAL),
            tls_policy=ctx.tls_policy,
            model=CrudeOilPricesRow,
            key="rows",
            params=params,
        )
        _bond_result, bond_rows = await fetch_rows(
            self._router,
            Capability.GOVERNMENT_BOND_YIELD,
            Symbol(id="", market=Market.US),
            tls_policy=ctx.tls_policy,
            model=GovernmentBondYieldRow,
            key="rows",
            params=params,
        )
        _fear_result, fear_rows = await fetch_rows(
            self._router,
            Capability.FEAR_GREED_INDEX,
            Symbol(id="", market=Market.US),
            tls_policy=ctx.tls_policy,
            model=FearGreedIndexRow,
            key="rows",
            params=params,
        )
        return GetMacroIndicatorsOutput(
            currentness_caveat=fx_result.currentness_caveat,
            exchange_rate=fx_rows,
            central_bank_rate=rate_rows,
            gold_price=gold_rows,
            crude_oil_prices=oil_rows,
            government_bond_yield=bond_rows,
            fear_greed_index=fear_rows,
        )


class GetBusinessIndicatorOutput(_RowsOutput):
    rows: tuple[BusinessIndicatorRow, ...] = Field(default_factory=tuple)


class GetBusinessIndicatorTool(BaseTool):
    """`get_business_indicator` — wraps `Capability.BUSINESS_INDICATOR` (景氣對策信號)."""

    name = "get_business_indicator"
    description = (
        "Get the whole-market monthly 景氣對策信號 "
        "(business-climate indicator) readings (Sponsor-tier)."
    )
    input_model: type[BaseModel] = MarketOnlyInput
    output_model: type[BaseModel] = GetBusinessIndicatorOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "business_indicator"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MarketOnlyInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.BUSINESS_INDICATOR,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=BusinessIndicatorRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetBusinessIndicatorOutput(
            currentness_caveat=result.currentness_caveat,
            rows=rows,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [
        (GetMacroIndicatorsTool(router), False),
        (GetBusinessIndicatorTool(router), False),
    ]


__all__ = [
    "GetBusinessIndicatorOutput",
    "GetBusinessIndicatorTool",
    "GetMacroIndicatorsInput",
    "GetMacroIndicatorsOutput",
    "GetMacroIndicatorsTool",
    "build_registration_table",
]
