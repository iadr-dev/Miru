from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import MaskedTeaser, ToolContext, dispatch_tool
from tools.common import DateRangeInput, MarketOnlyInput
from tools.derivatives_tools import (
    GetFutOptRosterTool,
    GetFuturesOptionsDailyTool,
    GetTaiexVixTool,
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


async def test_get_futopt_roster_market_level() -> None:
    symbol = Symbol(id="", market=Market.TW)
    row = {"market": "TW", "code": "TX", "contract_type": "futures", "name": "臺股期貨"}
    router, _ = make_router(symbol, {Capability.FUTOPT_INFO: _result({"rows": [row]})})
    tool = GetFutOptRosterTool(router)

    output = await tool.run(MarketOnlyInput(market=Market.TW), ToolContext())

    assert output.rows[0].code == "TX"


async def test_get_futures_options_daily_bundles_both_capabilities() -> None:
    symbol = Symbol(id="TXFA", market=Market.TW)
    futures_row = {
        "symbol": "TXFA",
        "market": "TW",
        "trade_date": "2026-08-01",
        "contract_date": "202608",
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 1.0,
        "spread": 0.0,
        "spread_per": 0.0,
        "volume": 1,
        "settlement_price": 1.0,
        "open_interest": 1,
        "trading_session": "regular",
    }
    router, adapter = make_router(
        symbol,
        {
            Capability.FUTURES_DAILY: _result({"rows": [futures_row]}),
            Capability.OPTION_DAILY: _result({"rows": []}),
        },
    )
    tool = GetFuturesOptionsDailyTool(router)

    output = await tool.run(DateRangeInput(id="TXFA", market=Market.TW), ToolContext())

    called_caps = {call[0] for call in adapter.calls}
    assert called_caps == {Capability.FUTURES_DAILY, Capability.OPTION_DAILY}
    assert output.futures[0].symbol == "TXFA"
    assert output.options == ()


async def test_get_taiex_vix_is_sponsor_gated() -> None:
    symbol = Symbol(id="", market=Market.TW)
    router, _ = make_router(
        symbol, {Capability.OPTION_VIX: _result({"rows": []}, data_class=DataClass.SPONSOR)}
    )
    tool = GetTaiexVixTool(router)
    assert tool.data_class is DataClass.SPONSOR
    args = MarketOnlyInput(market=Market.TW)

    masked = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))
    assert isinstance(masked, MaskedTeaser)
