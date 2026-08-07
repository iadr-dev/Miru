from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.common import DateRangeInput, MarketOnlyInput
from tools.technical_tools import (
    GetPriceLimitTool,
    GetPriceTickTool,
    GetTradingCalendarTool,
    GetWarrantUnderlyingTool,
)


def _result(
    payload: dict[str, object], *, data_class: DataClass = DataClass.PUBLIC
) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=data_class,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_trading_calendar_market_level() -> None:
    symbol = Symbol(id="", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.TRADING_CALENDAR: _result(
                {"rows": [{"market": "TW", "trade_date": "2026-08-01"}]}
            )
        },
    )
    tool = GetTradingCalendarTool(router)

    output = await tool.run(MarketOnlyInput(market=Market.TW), ToolContext())

    assert output.rows[0].trade_date.isoformat() == "2026-08-01"


async def test_get_price_limit_delegates() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    row = {
        "symbol": "2330",
        "market": "TW",
        "trade_date": "2026-08-01",
        "reference_price": 100.0,
        "limit_up": 110.0,
        "limit_down": 90.0,
    }
    router, adapter = make_router(symbol, {Capability.PRICE_LIMIT: _result({"rows": [row]})})
    tool = GetPriceLimitTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.PRICE_LIMIT
    assert output.rows[0].limit_up == 110.0


async def test_get_price_tick_is_sponsor() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(
        symbol, {Capability.PRICE_TICK: _result({"ticks": []}, data_class=DataClass.SPONSOR)}
    )
    tool = GetPriceTickTool(router)
    assert tool.data_class is DataClass.SPONSOR

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())
    assert output.rows == ()


async def test_get_warrant_underlying_is_sponsor() -> None:
    symbol = Symbol(id="038001", market=Market.TW)
    router, _ = make_router(
        symbol, {Capability.WARRANT_UNDERLYING: _result({"rows": []}, data_class=DataClass.SPONSOR)}
    )
    tool = GetWarrantUnderlyingTool(router)
    assert tool.data_class is DataClass.SPONSOR
    output = await tool.run(DateRangeInput(id="038001", market=Market.TW), ToolContext())
    assert output.rows == ()
