"""Keeping the screener's whole-market reads ready after each ingest.

The first screener request of a data day reads a 260-day price window or a statement history (a few
seconds, a few hundred MB of transient work). That cost lands on whichever user asks first. This
listens for the ingest's `data.updated` signal and recomputes, in the background, the reads this
process has already been asked for, so that user finds them ready.

Only reads already in use are refreshed (see `refresh_cached_bulk_reads`), so a worker nobody
screens on holds nothing extra. The refresh waits a settle time (an ingest lands several partitions
over minutes; one refresh after the last covers them all) plus a random spread (every API worker
hears the same signal, and reading the same files at once is what this is meant to avoid).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable

import structlog

from analytics.screener.engine import refresh_cached_bulk_reads
from events.bus import Event
from ingest.store import ParquetStore
from schemas.enums import Market

logger = structlog.get_logger(__name__)

SETTLE_SECONDS = 120.0
SPREAD_SECONDS = 180.0
_RETRY_SECONDS = 5.0

_Sleep = Callable[[float], Awaitable[None]]


def _is_tw_signal(event: Event) -> bool:
    return event.data.get("market") == Market.TW.value


class _Warmer:
    """The listener's state: at most one refresh is scheduled at a time, and a signal that arrives
    while one is scheduled and has not started is already covered by it."""

    def __init__(
        self,
        store: ParquetStore,
        *,
        settle_seconds: float,
        spread_seconds: float,
        sleep: _Sleep,
        spread: Callable[[], float],
    ) -> None:
        self._store = store
        self._settle_seconds = settle_seconds
        self._spread_seconds = spread_seconds
        self._sleep = sleep
        self._spread = spread
        self._pending: asyncio.Task[None] | None = None
        self._waiting = False  # scheduled and not started: a new signal is already covered

    async def _refresh_later(self) -> None:
        await self._sleep(self._settle_seconds + self._spread() * self._spread_seconds)
        self._waiting = False  # a signal from here on is for data the refresh below may not see
        try:
            refreshed = await refresh_cached_bulk_reads(self._store)
        except Exception:  # a failed warm-up costs the first request the read it always cost
            logger.warning("screener_warm_failed", exc_info=True)
            return
        if refreshed:
            logger.info("screener_warmed", reads=refreshed)

    async def listen(self, updates: Callable[[], AsyncIterator[Event]]) -> None:
        """Consume one opening of the signal feed; a feed that drops is logged, not raised."""
        try:
            async for event in updates():
                if _is_tw_signal(event) and not self._waiting:
                    self._waiting = True
                    self._pending = asyncio.create_task(self._refresh_later())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("screener_warm_feed_dropped", exc_info=True)

    def cancel_pending(self) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()


async def keep_screener_warm(
    updates: Callable[[], AsyncIterator[Event]],
    store: ParquetStore,
    *,
    settle_seconds: float = SETTLE_SECONDS,
    spread_seconds: float = SPREAD_SECONDS,
    sleep: _Sleep = asyncio.sleep,
    spread: Callable[[], float] = random.random,
) -> None:
    """Run until cancelled. `updates` opens the signal feed (reopened if it drops)."""
    warmer = _Warmer(
        store,
        settle_seconds=settle_seconds,
        spread_seconds=spread_seconds,
        sleep=sleep,
        spread=spread,
    )
    try:
        while True:
            await warmer.listen(updates)
            await sleep(_RETRY_SECONDS)
    finally:
        warmer.cancel_pending()


__all__ = ["keep_screener_warm"]
