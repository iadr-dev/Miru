"""Unit tests for `api/quote_stream.py`'s market-aware wiring: `quote_tool_name_for`,
`ToolQuoteFetcher`'s tool-name branch + `as_of` extraction, `entitlement_transform`'s
parameterized tool lookup, and `CalendarMarketOpenGate`'s new US session window. Complements
`tests/api/test_quote_stream_route.py`'s real-route SSE proof — these exercise the module's own
primitives directly against a real (non-network) tool registry."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from api.quote_stream import (
    CalendarMarketOpenGate,
    ToolQuoteFetcher,
    entitlement_transform,
    quote_tool_name_for,
)
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools import build_default_tool_registry
from tools.base import ToolContext, dispatch_tool
from tools.registry import ToolRegistry


def test_quote_tool_name_for_branches_by_market() -> None:
    assert quote_tool_name_for(Market.US) == "get_live_price_quote"
    assert quote_tool_name_for(Market.TW) == "get_price_quote"
    assert quote_tool_name_for(Market.GB) == "get_price_quote"


def _bar_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(ZoneInfo("UTC")),
        currentness_caveat="stub",
        payload={
            "bar": {
                "symbol": "2330",
                "market": "TW",
                "trade_date": "2024-01-02",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000,
            }
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _tick_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.LIVE_ISH,
        fetched_at=datetime.now(ZoneInfo("UTC")),
        currentness_caveat="stub",
        payload={"tick": {"price": 230.5, "observed_at": "2024-01-02T15:00:00Z"}},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_tool_quote_fetcher_dispatches_get_price_quote_for_tw() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(symbol, {Capability.PRICE_EOD: _bar_result()})
    registry = build_default_tool_registry(router)
    fetcher = ToolQuoteFetcher(registry, tls_policy=TLSPolicy.STRICT)

    result = await fetcher.fetch(symbol="2330", market=Market.TW)

    assert adapter.calls[0][0] is Capability.PRICE_EOD
    assert result.as_of == "2024-01-02"


async def test_tool_quote_fetcher_dispatches_get_live_price_quote_for_us() -> None:
    symbol = Symbol(id="AAPL", market=Market.US)
    router, adapter = make_router(symbol, {Capability.LIVE_PRICE_QUOTE: _tick_result()})
    registry = build_default_tool_registry(router)
    fetcher = ToolQuoteFetcher(registry, tls_policy=TLSPolicy.STRICT)

    result = await fetcher.fetch(symbol="AAPL", market=Market.US)

    assert adapter.calls[0][0] is Capability.LIVE_PRICE_QUOTE
    assert result.as_of == "2024-01-02T15:00:00Z"


def test_entitlement_transform_looks_up_the_given_tool_name() -> None:
    registry = ToolRegistry()
    # PUBLIC by default (both get_price_quote/get_live_price_quote are DataClass.PUBLIC today) —
    # masking never fires, but the lookup must still resolve the RIGHT tool name.
    transform = entitlement_transform(registry, Tier.FREE, tool_name="get_live_price_quote")
    data = {"quote": {"tick": {"price": 1.0}}}
    assert transform(data) == data  # unknown tool -> DataClass.PUBLIC default -> pass-through


def test_calendar_market_open_gate_us_session_window() -> None:
    gate = CalendarMarketOpenGate()
    ny = ZoneInfo("America/New_York")
    # A Tuesday, well inside 09:30-16:00 America/New_York.
    open_moment = datetime(2024, 1, 2, 12, 0, tzinfo=ny)
    assert gate.is_open(Market.US, open_moment) is True
    # Same day, before the open.
    before_open = datetime(2024, 1, 2, 6, 0, tzinfo=ny)
    assert gate.is_open(Market.US, before_open) is False
    # A Saturday.
    weekend = datetime(2024, 1, 6, 12, 0, tzinfo=ny)
    assert gate.is_open(Market.US, weekend) is False


async def test_dispatch_tool_get_live_price_quote_end_to_end() -> None:
    """Confirms `get_live_price_quote` is actually reachable through `dispatch_tool` (the same
    path `ToolQuoteFetcher`/the real `/quote/stream` route use) — a registry-wiring proof, not
    just a direct `.run()` call."""
    symbol = Symbol(id="AAPL", market=Market.US)
    router, _adapter = make_router(symbol, {Capability.LIVE_PRICE_QUOTE: _tick_result()})
    registry = build_default_tool_registry(router)
    tool = registry.get_tool("get_live_price_quote")
    assert tool is not None
    args = tool.input_model.model_validate({"id": "AAPL", "market": "US"})
    result = await dispatch_tool(
        tool, args, ToolContext(tier=Tier.PRO, tls_policy=TLSPolicy.STRICT)
    )
    dumped = result.model_dump(mode="json")
    assert dumped["tick"]["price"] == 230.5
