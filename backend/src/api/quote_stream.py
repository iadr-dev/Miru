"""Interface-layer wiring for the live-quote stream: the `QuoteFetcher` that drives the shared
poller through the unchanged `dispatch_tool`/`get_price_quote` path, the market-open gate over
the calendar, and the per-connection entitlement transform.

Keeping this in the interfaces layer (not `events/`) is what lets the poller/fan-out in `events/`
stay free of any `tools/` import: the actual quote fetch is exactly the one-shot `/quote` path —
`dispatch_tool` over the registered `get_price_quote` tool — and the entitlement gate that path
already applies is reused verbatim, never rebuilt.
"""

from __future__ import annotations

from calendar.market_hours import MarketHours
from collections.abc import Callable
from datetime import datetime
from datetime import time as time_
from typing import Protocol, cast, runtime_checkable
from zoneinfo import ZoneInfo

import structlog

from events.live_quote import QuoteFetchResult
from schemas.enums import DataClass, Market, Tier, TLSPolicy
from tools.base import MaskedTeaser, ToolContext, dispatch_tool, is_entitled
from tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)

_QUOTE_TOOL = "get_price_quote"
_LIVE_QUOTE_TOOL = "get_live_price_quote"

_NEW_YORK = ZoneInfo("America/New_York")


@runtime_checkable
class LiveQuoteManagerLike(Protocol):
    """The one method `api/app.py`'s shutdown hook needs off the yfinance live-quote manager
    singleton — a Protocol here (not an import of the concrete `sources.yfinance.
    live_quote_stream.YFinanceLiveQuoteManager`) because `sources/` may only be imported from
    `dataaccess/`/`ingest/` (enforced by `tests/arch/test_layering.py`), and `api/` is the
    interfaces layer."""

    async def aclose(self) -> None: ...


def quote_tool_name_for(market: Market) -> str:
    """Which one-shot quote tool the poller's `ToolQuoteFetcher` and the per-connection
    `entitlement_transform` should both dispatch through for `market` — `get_live_price_quote`
    (yfinance WebSocket) for US, the unchanged ingested-EOD `get_price_quote` otherwise."""
    return _LIVE_QUOTE_TOOL if market is Market.US else _QUOTE_TOOL


class CalendarMarketOpenGate:
    """`MarketOpenGate` over `calendar.market_hours.MarketHours` per market. Markets without a
    configured session fall back to a TW-shaped default rather than reporting perpetually open —
    the poller then idles the symbol off-hours, which is the safe (quota-preserving) direction.

    US uses the default plain-weekday `TradingDayCalendar()` (no US federal-holiday list plugged
    in) — a known, accepted degrade-gracefully gap, the same category as TW's own missing NCDR
    closure wiring: a market holiday will incorrectly read as open, wasting one idle poll rather
    than corrupting anything."""

    def __init__(self) -> None:
        self._by_market: dict[Market, MarketHours] = {
            Market.TW: MarketHours(),
            Market.US: MarketHours(
                tz=_NEW_YORK, session_open=time_(9, 30), session_close=time_(16, 0)
            ),
        }

    def is_open(self, market: Market, now: datetime) -> bool:
        hours = self._by_market.get(market)
        if hours is None:
            return False
        return hours.is_open(now)


class ToolQuoteFetcher:
    """Fetches one snapshot per tick through the same `dispatch_tool(...)` path the one-shot
    `/quote` uses — the poll is a *shared* wrapper around that unchanged fetch, not a new data
    path. Dispatches through `get_live_price_quote` for US (yfinance WebSocket) and the
    ingested-EOD `get_price_quote` for every other market — see `quote_tool_name_for`. The poll
    fetches with an entitled context so the channel carries real data once; each connection's own
    entitlement is applied at relay (see `entitlement_transform`), so one upstream fetch still
    serves a Free and a Premium watcher of the same symbol."""

    def __init__(self, registry: ToolRegistry, *, tls_policy: TLSPolicy) -> None:
        self._registry = registry
        self._tls_policy = tls_policy

    async def fetch(self, *, symbol: str, market: Market) -> QuoteFetchResult:
        tool_name = quote_tool_name_for(market)
        tool = self._registry.get_tool(tool_name)
        if tool is None:  # pragma: no cover — registry always has the quote tool
            return QuoteFetchResult(quote={"error": "quote_tool_unavailable"}, as_of=None)
        args = tool.input_model.model_validate({"id": symbol, "market": market.value})
        # Entitled poll context: real data on the shared channel, masked per-connection at relay.
        ctx = ToolContext(tier=Tier.PRO, tls_policy=self._tls_policy)
        result = await dispatch_tool(tool, args, ctx)
        quote = result.model_dump(mode="json")
        return QuoteFetchResult(quote=quote, as_of=_extract_as_of(quote, tool_name))


def _extract_as_of(quote: dict[str, object], tool_name: str) -> str | None:
    """The snapshot's own source timestamp. `get_price_quote`'s bar carries `trade_date`
    (the served value's currency, not the publish time); `get_live_price_quote`'s tick carries
    its own `observed_at`. Absent (masked/empty) → `None`."""
    if tool_name == _LIVE_QUOTE_TOOL:
        tick = quote.get("tick")
        if isinstance(tick, dict):
            observed_at = cast("dict[str, object]", tick).get("observed_at")
            if isinstance(observed_at, str):
                return observed_at
        return None
    bar = quote.get("bar")
    if isinstance(bar, dict):
        trade_date = cast("dict[str, object]", bar).get("trade_date")
        if isinstance(trade_date, str):
            return trade_date
    return None


def entitlement_transform(
    registry: ToolRegistry, tier: Tier, *, tool_name: str = _QUOTE_TOOL
) -> Callable[[dict[str, object]], dict[str, object]]:
    """A per-connection frame transform that re-applies `tool_name`'s own entitlement gate to
    each shared tick. If the deployed quote tool is sponsor-classed and the connection's tier
    isn't entitled, the real `quote` is replaced with the same `MaskedTeaser` shape
    `dispatch_tool` would have substituted — never a hard error, and computed per connection so
    the shared fetch is unaffected. `tool_name` defaults to `get_price_quote` for callers that
    don't need the US/`get_live_price_quote` branch (e.g. a test exercising the TW-only shape);
    `quote_stream()` (the real route) always passes `quote_tool_name_for(market)` explicitly."""
    tool = registry.get_tool(tool_name)
    data_class = tool.data_class if tool is not None else DataClass.PUBLIC

    def _transform(data: dict[str, object]) -> dict[str, object]:
        if data_class is DataClass.SPONSOR and not is_entitled(data_class, tier):
            masked = MaskedTeaser(
                required_tier=Tier.PREMIUM,
                # User-facing text — never the raw internal tool name, matching
                # `tools/base.py::dispatch_tool`'s own default masked-teaser preview text.
                preview="This data is available on a Premium or Pro subscription.",
                tool_name=tool_name,
            )
            return {**data, "quote": masked.model_dump(mode="json")}
        return data

    return _transform


__all__ = [
    "CalendarMarketOpenGate",
    "LiveQuoteManagerLike",
    "ToolQuoteFetcher",
    "entitlement_transform",
    "quote_tool_name_for",
]
