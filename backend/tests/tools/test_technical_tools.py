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
    GetValuationRatiosTool,
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


async def test_get_valuation_ratios_computes_band_summary() -> None:
    """Regression guard for the valuation-band zero-prose bug (same class as Finding #69): a
    ~year-long daily window's raw rows can consume the entire model-facing render-size budget,
    leaving the model unable to answer "is current PER high or low in-range" at all. `band` must
    carry the pre-computed min/max/current so the model can cite it directly."""
    symbol = Symbol(id="2330", market=Market.TW)
    rows = [
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": f"2026-08-{day:02d}",
            "dividend_yield": yield_,
            "per": per,
            "pbr": pbr,
        }
        for day, yield_, per, pbr in [
            (1, 1.5, 18.0, 5.0),
            (2, 1.4, 22.0, 5.5),
            (3, 1.6, 15.0, 4.8),
            (4, 1.3, 20.0, 5.2),
        ]
    ]
    router, _adapter = make_router(symbol, {Capability.VALUATION_RATIOS: _result({"rows": rows})})
    tool = GetValuationRatiosTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert output.band is not None
    assert output.band.per_min == 15.0
    assert output.band.per_max == 22.0
    assert output.band.per_current == 20.0  # the LATEST row (day 4), not an average
    assert output.band.pbr_min == 4.8
    assert output.band.pbr_max == 5.5
    assert output.band.dividend_yield_current == 1.3
    assert output.band.window_start.isoformat() == "2026-08-01"
    assert output.band.window_end.isoformat() == "2026-08-04"


async def test_get_valuation_ratios_band_is_none_when_no_rows() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _adapter = make_router(symbol, {Capability.VALUATION_RATIOS: _result({"rows": []})})
    tool = GetValuationRatiosTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert output.band is None
    assert output.rows == ()


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
