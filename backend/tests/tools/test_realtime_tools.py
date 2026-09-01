from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import MaskedTeaser, ToolContext, dispatch_tool
from tools.common import SymbolInput
from tools.realtime_tools import (
    GetFuturesSnapshotTool,
    GetOptionsSnapshotTool,
    GetTickSnapshotTool,
)


def _result(
    payload: dict[str, object], *, data_class: DataClass = DataClass.SPONSOR
) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=data_class,
        timing_class=TimingClass.LIVE_ISH,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_tick_snapshot_is_sponsor_gated() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    row = {
        "symbol": "2330",
        "market": "TW",
        "trade_date": "2026-08-21",
        "amount": 1000.0,
        "average_price": 100.0,
        "buy_price": 99.5,
        "buy_volume": 10,
        "change_price": 1.0,
        "change_rate": 1.0,
        "close": 100.0,
        "high": 101.0,
        "low": 99.0,
        "open": 99.5,
        "sell_price": 100.5,
        "sell_volume": 5,
        "total_amount": 100000.0,
        "total_volume": 1000,
        "volume": 10,
        "volume_ratio": 1.0,
        "yesterday_volume": 900,
        "tick_type": "1",
    }
    router, _ = make_router(symbol, {Capability.TICK_SNAPSHOT: _result({"rows": [row]})})
    tool = GetTickSnapshotTool(router)
    assert tool.data_class is DataClass.SPONSOR
    args = SymbolInput(id="2330", market=Market.TW)

    masked = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))
    assert isinstance(masked, MaskedTeaser)

    unmasked = await dispatch_tool(tool, args, ToolContext(tier=Tier.PREMIUM))
    assert not isinstance(unmasked, MaskedTeaser)


async def test_get_futures_snapshot_reads_rows() -> None:
    symbol = Symbol(id="TXFA", market=Market.TW)
    row = {
        "symbol": "TXFA",
        "market": "TW",
        "trade_date": "2026-08-21",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "change_price": 0.5,
        "change_rate": 0.5,
        "average_price": 100.2,
        "volume": 10,
        "total_volume": 1000,
        "amount": 1000.0,
        "total_amount": 100000.0,
        "yesterday_volume": 900,
        "buy_price": 100.0,
        "buy_volume": 5,
        "sell_price": 100.5,
        "sell_volume": 5,
        "volume_ratio": 1.0,
        "tick_type": "1",
    }
    router, adapter = make_router(symbol, {Capability.FUTURES_SNAPSHOT: _result({"rows": [row]})})
    tool = GetFuturesSnapshotTool(router)

    output = await tool.run(SymbolInput(id="TXFA", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.FUTURES_SNAPSHOT
    assert output.rows[0].symbol == "TXFA"


async def test_get_options_snapshot_reads_rows() -> None:
    symbol = Symbol(id="TXOA", market=Market.TW)
    row = {
        "symbol": "TXOA",
        "market": "TW",
        "trade_date": "2026-08-21",
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "change_price": 0.5,
        "change_rate": 5.0,
        "average_price": 10.2,
        "volume": 10,
        "total_volume": 1000,
        "amount": 100.0,
        "total_amount": 10000.0,
        "yesterday_volume": 900,
        "buy_price": 10.0,
        "buy_volume": 5,
        "sell_price": 10.5,
        "sell_volume": 5,
        "volume_ratio": 1.0,
        "tick_type": "1",
    }
    router, adapter = make_router(symbol, {Capability.OPTIONS_SNAPSHOT: _result({"rows": [row]})})
    tool = GetOptionsSnapshotTool(router)

    output = await tool.run(SymbolInput(id="TXOA", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.OPTIONS_SNAPSHOT
    assert output.rows[0].symbol == "TXOA"
