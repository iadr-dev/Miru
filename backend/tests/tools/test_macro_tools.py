from __future__ import annotations

from datetime import UTC, datetime

import pytest

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
    GetMacroIndicatorsOutput,
    GetMacroIndicatorsTool,
)


def _result(payload: dict[str, object], status: HealthStatus = HealthStatus.OK) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=status, detail="x"),
    )


@pytest.mark.parametrize(
    ("gold_status", "expected"),
    [
        (HealthStatus.OK, HealthStatus.OK),
        (HealthStatus.STALE, HealthStatus.STALE),
        (HealthStatus.UNREACHABLE, HealthStatus.UNREACHABLE),
    ],
)
async def test_macro_indicators_report_the_worst_section_health(
    gold_status: HealthStatus, expected: HealthStatus
) -> None:
    caps = {
        (Capability.EXCHANGE_RATE, Market.TW): "exchange_rate",
        (Capability.CENTRAL_BANK_RATE, Market.GLOBAL): "central_bank_rate",
        (Capability.GOLD_PRICE, Market.GLOBAL): "gold_price",
        (Capability.CRUDE_OIL_PRICES, Market.GLOBAL): "crude_oil_prices",
        (Capability.GOVERNMENT_BOND_YIELD, Market.US): "government_bond_yield",
    }
    results = {
        cap: _result({"rows": []}, gold_status if cap is Capability.GOLD_PRICE else HealthStatus.OK)
        for cap, _market in caps
    }
    adapter = StubAdapter("STUB", supported=set(caps), results=results)
    priority: dict[str, dict[str, list[str]]] = {}
    for (cap, market), name in caps.items():
        priority.setdefault(market.value, {})[name] = ["STUB"]
        assert cap.value == name
    router = DataAccessRouter({"STUB": adapter}, priority_config=priority)

    out = await GetMacroIndicatorsTool(router).run(GetMacroIndicatorsInput(), ToolContext())

    assert out.health_status is expected  # type: ignore[attr-defined]


async def test_business_indicator_reports_health() -> None:
    symbol = Symbol(id="", market=Market.TW)
    router, _ = make_router(
        symbol, {Capability.BUSINESS_INDICATOR: _result({"rows": []}, HealthStatus.UNREACHABLE)}
    )
    out = await GetBusinessIndicatorTool(router).run(
        MarketOnlyInput(market=Market.TW), ToolContext()
    )
    assert out.health_status is HealthStatus.UNREACHABLE  # type: ignore[attr-defined]


async def test_get_macro_indicators_is_public_with_fear_greed_for_paid_only() -> None:
    """The macro tool fans out across TW/GLOBAL/US in one call — `make_router` only scopes
    its stub to one market at a time, so this test builds the router directly with one stub
    adapter declaring all three markets. The tool is public (every dataset but the Fear & Greed
    Index is free); the Sponsor-tier index is fetched only for an entitled caller."""
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
    assert tool.data_class is DataClass.PUBLIC
    args = GetMacroIndicatorsInput()

    free = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))
    assert isinstance(free, GetMacroIndicatorsOutput)
    assert free.exchange_rate[0].symbol == "USD"
    assert free.central_bank_rate == ()
    fetched = [cap for cap, _sym, _params in adapter.calls]
    assert Capability.FEAR_GREED_INDEX not in fetched

    await dispatch_tool(tool, args, ToolContext(tier=Tier.PREMIUM))
    fetched = [cap for cap, _sym, _params in adapter.calls]
    assert Capability.FEAR_GREED_INDEX in fetched


async def test_get_business_indicator_is_sponsor_gated() -> None:
    symbol = Symbol(id="", market=Market.TW)
    router, _ = make_router(symbol, {Capability.BUSINESS_INDICATOR: _result({"rows": []})})
    tool = GetBusinessIndicatorTool(router)
    assert tool.data_class is DataClass.SPONSOR
    args = MarketOnlyInput(market=Market.TW)

    masked = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))
    assert isinstance(masked, MaskedTeaser)
