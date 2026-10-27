from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.common import DateRangeInput, MarketOnlyInput
from tools.technical_tools import (
    GetPriceLimitTool,
    GetPriceTickTool,
    GetTechnicalIndicatorsTool,
    GetTotalReturnIndexTool,
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


async def test_get_valuation_ratios_attaches_a_point_chart_hint_when_rows_exist() -> None:
    """`per`/`pbr` are two independent quantitative fields on the same row — the first real
    caller of the `point` (scatter) mark."""
    symbol = Symbol(id="2330", market=Market.TW)
    row = {
        "symbol": "2330",
        "market": "TW",
        "trade_date": "2026-08-01",
        "dividend_yield": 1.5,
        "per": 18.0,
        "pbr": 5.0,
    }
    router, _adapter = make_router(symbol, {Capability.VALUATION_RATIOS: _result({"rows": [row]})})
    tool = GetValuationRatiosTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert output.chart_hint is not None
    assert output.chart_hint.mark.value == "point"
    assert output.chart_hint.x is not None
    assert output.chart_hint.x.field == "per"
    assert output.chart_hint.y is not None
    assert output.chart_hint.y.field == "pbr"


async def test_get_valuation_ratios_omits_chart_hint_when_no_rows() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _adapter = make_router(symbol, {Capability.VALUATION_RATIOS: _result({"rows": []})})
    tool = GetValuationRatiosTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert output.chart_hint is None


async def test_get_total_return_index_attaches_a_line_chart_hint_when_rows_exist() -> None:
    """A single continuous index-level series over time is the textbook `line` case — the first
    real caller of that mark."""
    symbol = Symbol(id="TAIEX", market=Market.TW)
    row = {"symbol": "TAIEX", "market": "TW", "trade_date": "2026-08-01", "price": 12345.6}
    router, _adapter = make_router(
        symbol, {Capability.TOTAL_RETURN_INDEX: _result({"rows": [row]})}
    )
    tool = GetTotalReturnIndexTool(router)

    output = await tool.run(DateRangeInput(id="TAIEX", market=Market.TW), ToolContext())

    assert output.chart_hint is not None
    assert output.chart_hint.mark.value == "line"
    assert output.chart_hint.x is not None
    assert output.chart_hint.x.field == "trade_date"
    assert output.chart_hint.y is not None
    assert output.chart_hint.y.field == "price"


async def test_get_total_return_index_omits_chart_hint_when_no_rows() -> None:
    symbol = Symbol(id="TAIEX", market=Market.TW)
    router, _adapter = make_router(symbol, {Capability.TOTAL_RETURN_INDEX: _result({"rows": []})})
    tool = GetTotalReturnIndexTool(router)

    output = await tool.run(DateRangeInput(id="TAIEX", market=Market.TW), ToolContext())

    assert output.chart_hint is None


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


def _eod_bar(day: str, *, open_: float, high: float, low: float, close: float) -> dict[str, object]:
    return {
        "symbol": "2330",
        "market": "TW",
        "trade_date": day,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000,
    }


async def test_get_technical_indicators_computes_series() -> None:
    """A 65-bar rising series is enough to warm up MA60 (but not MA120/240, which stay all-`None`
    — never fabricated) and to exercise KD/MACD/Bollinger/support-resistance/pattern scanning
    over the same fetched window."""
    symbol = Symbol(id="2330", market=Market.TW)
    bars = [
        _eod_bar(
            f"2026-{1 + (i // 28):02d}-{1 + (i % 28):02d}",
            open_=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
        )
        for i in range(65)
    ]
    router, adapter = make_router(symbol, {Capability.PRICE_EOD: _result({"bars": bars})})
    tool = GetTechnicalIndicatorsTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.PRICE_EOD
    assert output.data_available is True
    assert len(output.bars) == 65
    assert len(output.trade_dates) == 65
    assert output.moving_averages is not None
    assert output.moving_averages.ma60.values[-1] is not None
    assert output.moving_averages.ma120.values[-1] is None
    assert output.bollinger is not None
    assert output.kd is not None
    assert output.macd is not None
    assert output.support_resistance is not None
    # A monotonically-rising series has no interior swing-low/high pivot.
    assert output.support_resistance.levels == ()
    assert output.chart_patterns == ()


async def test_get_technical_indicators_reports_chart_patterns() -> None:
    """A double top (peaks 110 / 110 around a 100 trough) comes back as an additive
    `chart_patterns` hit with its pivots, neckline and a `forming` status."""
    symbol = Symbol(id="2330", market=Market.TW)
    key_points = [(0, 90.0), (20, 110.0), (40, 100.0), (60, 110.0), (70, 104.0)]
    closes = [
        y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        for (x0, y0), (x1, y1) in itertools.pairwise(key_points)
        for x in range(x0, x1)
    ] + [104.0]
    first_day = date(2026, 1, 1)
    bars = [
        _eod_bar(
            (first_day + timedelta(days=i)).isoformat(),
            open_=c,
            high=c * 1.003,
            low=c * 0.997,
            close=c,
        )
        for i, c in enumerate(closes)
    ]
    router, _ = make_router(symbol, {Capability.PRICE_EOD: _result({"bars": bars})})
    tool = GetTechnicalIndicatorsTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    (hit,) = [h for h in output.chart_patterns if h.pattern == "double_top"]
    assert hit.status == "forming"
    assert hit.family == "reversal_top"
    assert [p.bar_index for p in hit.pivots] == [20, 40, 60]
    assert hit.pivots[0].trade_date == output.trade_dates[20]
    assert hit.lines[0].name == "neckline"


async def test_get_technical_indicators_no_bars_is_unavailable() -> None:
    symbol = Symbol(id="9999", market=Market.TW)
    router, _ = make_router(symbol, {Capability.PRICE_EOD: _result({"bars": []})})
    tool = GetTechnicalIndicatorsTool(router)

    output = await tool.run(DateRangeInput(id="9999", market=Market.TW), ToolContext())

    assert output.data_available is False
    assert output.moving_averages is None
    assert output.unavailable_reason is not None
