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
from typing import cast

import structlog

from events.live_quote import QuoteFetchResult
from schemas.enums import DataClass, Market, Tier, TLSPolicy
from tools.base import MaskedTeaser, ToolContext, dispatch_tool, is_entitled
from tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)

_QUOTE_TOOL = "get_price_quote"


class CalendarMarketOpenGate:
    """`MarketOpenGate` over `calendar.market_hours.MarketHours` per market. Markets without a
    configured session fall back to a TW-shaped default rather than reporting perpetually open —
    the poller then idles the symbol off-hours, which is the safe (quota-preserving) direction."""

    def __init__(self) -> None:
        self._by_market: dict[Market, MarketHours] = {Market.TW: MarketHours()}

    def is_open(self, market: Market, now: datetime) -> bool:
        hours = self._by_market.get(market)
        if hours is None:
            return False
        return hours.is_open(now)


class ToolQuoteFetcher:
    """Fetches one snapshot per tick through the same `dispatch_tool(get_price_quote)` path the
    one-shot `/quote` uses — the poll is a *shared* wrapper around that unchanged fetch, not a new
    data path. The poll fetches with an entitled context so the channel carries real data once;
    each connection's own entitlement is applied at relay (see `entitlement_transform`), so one
    upstream fetch still serves a Free and a Premium watcher of the same symbol."""

    def __init__(self, registry: ToolRegistry, *, tls_policy: TLSPolicy) -> None:
        self._registry = registry
        self._tls_policy = tls_policy

    async def fetch(self, *, symbol: str, market: Market) -> QuoteFetchResult:
        tool = self._registry.get_tool(_QUOTE_TOOL)
        if tool is None:  # pragma: no cover — registry always has the quote tool
            return QuoteFetchResult(quote={"error": "quote_tool_unavailable"}, as_of=None)
        args = tool.input_model.model_validate({"id": symbol, "market": market.value})
        # Entitled poll context: real data on the shared channel, masked per-connection at relay.
        ctx = ToolContext(tier=Tier.PRO, tls_policy=self._tls_policy)
        result = await dispatch_tool(tool, args, ctx)
        quote = result.model_dump(mode="json")
        return QuoteFetchResult(quote=quote, as_of=_extract_as_of(quote))


def _extract_as_of(quote: dict[str, object]) -> str | None:
    """The snapshot's own source timestamp — the quote tool's bar carries `trade_date`, which is
    the served value's currency (not the publish time). Absent (masked/empty) → `None`."""
    bar = quote.get("bar")
    if isinstance(bar, dict):
        trade_date = cast("dict[str, object]", bar).get("trade_date")
        if isinstance(trade_date, str):
            return trade_date
    return None


def entitlement_transform(
    registry: ToolRegistry, tier: Tier
) -> Callable[[dict[str, object]], dict[str, object]]:
    """A per-connection frame transform that re-applies the `get_price_quote` tool's own
    entitlement gate to each shared tick. If the deployed quote tool is sponsor-classed and the
    connection's tier isn't entitled, the real `quote` is replaced with the same `MaskedTeaser`
    shape `dispatch_tool` would have substituted — never a hard error, and computed per
    connection so the shared fetch is unaffected."""
    tool = registry.get_tool(_QUOTE_TOOL)
    data_class = tool.data_class if tool is not None else DataClass.PUBLIC

    def _transform(data: dict[str, object]) -> dict[str, object]:
        if data_class is DataClass.SPONSOR and not is_entitled(data_class, tier):
            masked = MaskedTeaser(
                required_tier=Tier.PREMIUM,
                preview=f"{_QUOTE_TOOL} requires a Premium or Pro subscription to unlock.",
                tool_name=_QUOTE_TOOL,
            )
            return {**data, "quote": masked.model_dump(mode="json")}
        return data

    return _transform


__all__ = [
    "CalendarMarketOpenGate",
    "ToolQuoteFetcher",
    "entitlement_transform",
]
