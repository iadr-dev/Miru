from __future__ import annotations

from datetime import UTC, datetime

from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import StubAdapter, make_router
from tools.base import MaskedTeaser, ToolContext, dispatch_tool
from tools.common import MarketOnlyInput
from tools.macro_tools import (
    GetBusinessIndicatorTool,
    GetMacroIndicatorsInput,
    GetMacroIndicatorsTool,
)


def _result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_macro_indicators_bundles_six_datasets_and_is_sponsor_gated() -> None:
    """The macro tool fans out across TW/GLOBAL/US in one call — `make_router` only scopes
    its stub to one market at a time, so this test builds the router directly with one stub
    adapter declaring all three markets."""
    fx_row = {
        "symbol": "USD",
        "market": "TW",
        "trade_date": "2026-08-01",
        "cash_buy": 1.0,
        "cash_sell": 1.0,
        "spot_buy": 1.0,
        "spot_sell": 1.0,
    }
    results = {
        Capability.EXCHANGE_RATE: _result({"rows": [fx_row]}),
        Capability.CENTRAL_BANK_RATE: _result({"rows": []}),
        Capability.GOLD_PRICE: _result({"rows": []}),
        Capability.CRUDE_OIL_PRICES: _result({"rows": []}),
        Capability.GOVERNMENT_BOND_YIELD: _result({"rows": []}),
        Capability.FEAR_GREED_INDEX: _result({"rows": []}),
    }
    supported = {
        (Capability.EXCHANGE_RATE, Market.TW),
        (Capability.CENTRAL_BANK_RATE, Market.GLOBAL),
        (Capability.GOLD_PRICE, Market.GLOBAL),
        (Capability.CRUDE_OIL_PRICES, Market.GLOBAL),
        (Capability.GOVERNMENT_BOND_YIELD, Market.US),
        (Capability.FEAR_GREED_INDEX, Market.US),
    }
    adapter = StubAdapter("STUB", supported=supported, results=results)
    priority = {
        "TW": {"exchange_rate": ["STUB"]},
        "GLOBAL": {
            "central_bank_rate": ["STUB"],
            "gold_price": ["STUB"],
            "crude_oil_prices": ["STUB"],
        },
        "US": {"government_bond_yield": ["STUB"], "fear_greed_index": ["STUB"]},
    }
    router = DataAccessRouter({"STUB": adapter}, priority_config=priority)

    tool = GetMacroIndicatorsTool(router)
    assert tool.data_class is DataClass.SPONSOR
    args = GetMacroIndicatorsInput()

    masked = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))
    assert isinstance(masked, MaskedTeaser)

    output = await tool.run(args, ToolContext())
    assert output.exchange_rate[0].symbol == "USD"
    assert output.central_bank_rate == ()


async def test_get_business_indicator_is_sponsor_gated() -> None:
    symbol = Symbol(id="", market=Market.TW)
    router, _ = make_router(symbol, {Capability.BUSINESS_INDICATOR: _result({"rows": []})})
    tool = GetBusinessIndicatorTool(router)
    assert tool.data_class is DataClass.SPONSOR
    args = MarketOnlyInput(market=Market.TW)

    masked = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))
    assert isinstance(masked, MaskedTeaser)
