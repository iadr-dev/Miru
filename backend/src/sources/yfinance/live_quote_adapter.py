"""`YFinanceLiveQuoteAdapter` — the `SourceAdapter` for `Capability.LIVE_PRICE_QUOTE`.

A wholly separate class from `YFinanceAdapter`, not a branch inside it: that class is explicitly
documented as touching no instance state (every domain handler is a pure function of its
arguments), and this adapter must hold the `YFinanceLiveQuoteManager` singleton — mixing that in
would break `YFinanceAdapter`'s own invariant for no benefit, since nothing about the manager's
subscribe/cache lifecycle fits that class's stateless dispatch-table shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import anyio.to_thread

from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult

from ._blocking import _fetch_info_blocking
from ._shared import _as_float, _as_int, _get, _yahoo_symbol
from .live_quote_stream import YFinanceLiveQuoteManager

_CAVEAT_OK = (
    "streamed via yfinance's WebSocket (Yahoo Finance real-time feed) — reference-only, "
    "attributed content, not our recommendation; unofficial API, coverage varies by symbol."
)
_CAVEAT_SNAPSHOT = (
    "no live WebSocket tick has arrived yet for this symbol (market may be closed) — this is a "
    "one-shot Yahoo Finance snapshot (last regular-session price), not a streaming tick; "
    "reference-only, attributed content, not our recommendation."
)
_CAVEAT_UNAVAILABLE = (
    "no live tick and no snapshot available for this symbol — surfaced via source health, not "
    "a fabricated price."
)


# The exact field set `tools/market_tools.py::LivePriceTick` declares (`extra="forbid"`) — the
# raw decoded `PricingData` WS message carries several fields that model doesn't map
# (`id`/`time`/`exchange`/`quote_type`/`last_size`/`price_hint`/...), so a live tick must be
# narrowed to this set before crossing the tool boundary, same as `_snapshot_tick`'s own
# `.info`-to-tick mapping does for the off-hours fallback.
_LIVE_TICK_FIELDS = (
    "price",
    "change",
    "change_percent",
    "day_high",
    "day_low",
    "day_volume",
    "open_price",
    "previous_close",
    "bid",
    "bid_size",
    "ask",
    "ask_size",
    "currency",
    "market_hours",
)


def _narrow_live_tick(raw: dict[str, object]) -> dict[str, object]:
    return {key: raw[key] for key in _LIVE_TICK_FIELDS if key in raw}


def _snapshot_tick(info: dict[str, Any]) -> dict[str, object] | None:
    """A one-shot `.info` read maps onto the SAME tick shape the WebSocket produces (`price`/
    `change`/`day_high`/etc.) — this is the off-hours/pre-first-tick fallback: `.info`'s
    `regularMarketPrice` is the last-known regular-session price regardless of whether the
    session is currently open, unlike the WS cache (which is only ever populated while a tick has
    actually streamed in). `None` when yfinance itself has no price for this symbol at all."""
    price = _as_float(_get(info, "regularMarketPrice"))
    if price is None:
        return None
    previous_close = _as_float(_get(info, "previousClose"))
    change = price - previous_close if previous_close is not None else None
    return {
        "price": price,
        "change": change,
        # `regularMarketChangePercent` is already percent-scaled (e.g. `-0.196` means -0.196%) —
        # the SAME scale the real WebSocket tick's own `change_percent` field uses (confirmed
        # live: a tick carrying `change=-0.67`/`price=335.9` reported `change_percent=-0.199`,
        # not `-0.00199`).
        "change_percent": _as_float(_get(info, "regularMarketChangePercent")),
        "day_high": _as_float(_get(info, "regularMarketDayHigh")),
        "day_low": _as_float(_get(info, "regularMarketDayLow")),
        "day_volume": _as_int(_get(info, "regularMarketVolume")),
        "open_price": _as_float(_get(info, "regularMarketOpen")),
        "previous_close": previous_close,
        "bid": _as_float(_get(info, "bid")),
        "bid_size": _as_int(_get(info, "bidSize")),
        "ask": _as_float(_get(info, "ask")),
        "ask_size": _as_int(_get(info, "askSize")),
        "currency": _get(info, "currency"),
        "market_hours": None,
    }


class YFinanceLiveQuoteAdapter:
    """The `YFINANCE_LIVE` source adapter — US-only, backed by one shared WebSocket connection
    (see `live_quote_stream.py`)."""

    source_id = "YFINANCE_LIVE"
    timing_class = TimingClass.LIVE_ISH

    def __init__(self, manager: YFinanceLiveQuoteManager | None = None) -> None:
        self._manager = manager or YFinanceLiveQuoteManager()

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.LIVE_PRICE_QUOTE, Market.US)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        """Contract is trivially satisfied by the type signature; kept for Protocol parity with
        other adapters (matches `YFinanceAdapter.validate`'s own docstring reasoning)."""
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        if cap is not Capability.LIVE_PRICE_QUOTE or sym.market is not Market.US:
            return self._result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail=f"YFINANCE_LIVE does not serve {cap.value} for {sym.market.value}",
                ),
            )
        yahoo_symbol = _yahoo_symbol(sym)
        await self._manager.subscribe(yahoo_symbol)
        tick = self._manager.latest(yahoo_symbol)
        if tick is not None:
            return self._result(
                payload={"tick": _narrow_live_tick(tick)},
                health=SourceHealth(status=HealthStatus.OK, detail="ok"),
            )
        # No streamed tick yet (common off-hours, or before the very first tick after subscribe)
        # — fall back to a one-shot `.info` snapshot rather than surfacing an empty price. `.info`
        # always carries `regularMarketPrice` (the last regular-session trade) regardless of
        # whether the session is open right now, unlike the WS cache.
        info = await anyio.to_thread.run_sync(_fetch_info_blocking, yahoo_symbol)
        snapshot = _snapshot_tick(info)
        if snapshot is None:
            return self._result(
                payload={},
                health=SourceHealth(status=HealthStatus.STALE, detail=_CAVEAT_UNAVAILABLE),
            )
        return self._result(
            payload={"tick": snapshot},
            health=SourceHealth(status=HealthStatus.STALE, detail=_CAVEAT_SNAPSHOT),
        )

    def _result(self, *, payload: dict[str, object], health: SourceHealth) -> SourceResult:
        return SourceResult(
            source_id=self.source_id,
            data_class=DataClass.PUBLIC,
            timing_class=self.timing_class,
            fetched_at=datetime.now(UTC),
            currentness_caveat=_CAVEAT_OK if health.status is HealthStatus.OK else health.detail,
            payload=payload,
            health=health,
        )


__all__ = ["YFinanceLiveQuoteAdapter"]
