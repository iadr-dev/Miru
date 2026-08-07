from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.common import DateRangeInput
from tools.international_tools import (
    FinMindInternationalMarket,
    GetInternationalStockInput,
    GetInternationalStockTool,
    GetUsStockPriceMinuteTool,
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


async def test_get_international_stock_resolves_market_and_bundles_info_price() -> None:
    symbol = Symbol(id="^DJI", market=Market.US)
    info_row = {"symbol": "^DJI", "market": "US", "as_of": "2026-08-01", "name": "Dow Jones"}
    price_row = {
        "symbol": "^DJI",
        "market": "US",
        "trade_date": "2026-08-01",
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 1.0,
        "adj_close": 1.0,
        "volume": 1,
    }
    router, adapter = make_router(
        symbol,
        {
            Capability.US_STOCK_INFO: _result({"rows": [info_row]}),
            Capability.US_STOCK_PRICE: _result({"rows": [price_row]}),
        },
    )
    tool = GetInternationalStockTool(router)

    output = await tool.run(
        GetInternationalStockInput(id="^DJI", market=FinMindInternationalMarket.US),
        ToolContext(),
    )

    called_caps = {call[0] for call in adapter.calls}
    assert called_caps == {Capability.US_STOCK_INFO, Capability.US_STOCK_PRICE}
    assert output.info[0].name == "Dow Jones"
    assert output.price[0].close == 1.0


async def test_get_us_stock_price_minute_is_sponsor_gated() -> None:
    symbol = Symbol(id="AAPL", market=Market.US)
    router, _ = make_router(
        symbol,
        {Capability.US_STOCK_PRICE_MINUTE: _result({"rows": []})},
    )
    tool = GetUsStockPriceMinuteTool(router)
    assert tool.data_class is DataClass.SPONSOR

    output = await tool.run(DateRangeInput(id="AAPL", market=Market.US), ToolContext())
    assert output.rows == ()
