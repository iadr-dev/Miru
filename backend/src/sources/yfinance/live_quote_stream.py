"""The yfinance WebSocket live-quote manager — a per-process singleton owning one shared
streaming connection.

Every other yfinance touch in this codebase (`_blocking.py`) is a stateless one-shot blocking
call wrapped in `anyio.to_thread.run_sync`. This is deliberately different: `yfinance.live.
AsyncWebSocket` is a persistent, fully-async connection with no blocking calls to thread-wrap —
there is no existing "long-lived connection owned by a source" precedent anywhere in `sources/`,
so this mirrors the closest available shape instead: `events/live_quote.py::QuotePoller`'s
demand-driven task lifecycle (start on first subscriber, self-terminate — here, stay open but
idle — when nothing is subscribed, force-cancel via `aclose()`). Every background task in this
codebase, including this one, uses raw `asyncio.create_task` + manual cancel-and-await; there is
no generic task-supervision helper to reuse.

Subscribe is refcounted so N callers watching the same symbol share one upstream subscription.
Unlike the SSE `/quote/stream` path (which has a natural unsubscribe trigger — connection
disconnect), a one-shot tool call (`get_live_price_quote`) has no "done watching" signal, so an
idle-symbol sweep unsubscribes anything nobody has touched in a while — see `_sweep_idle`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Protocol, cast, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)

_IDLE_SWEEP_INTERVAL_SECONDS = 60.0
_IDLE_UNSUBSCRIBE_AFTER_SECONDS = 300.0


@runtime_checkable
class _LiveWebSocket(Protocol):
    """The subset of `yfinance.live.AsyncWebSocket`'s surface this manager drives — named here
    so a test can inject a fake without importing the real (network-capable) SDK class."""

    async def subscribe(self, symbols: list[str]) -> None: ...

    async def unsubscribe(self, symbols: list[str]) -> None: ...

    async def listen(
        self, message_handler: Callable[[dict[str, object]], Awaitable[None]]
    ) -> None: ...

    async def close(self) -> None: ...


def _default_ws_factory() -> _LiveWebSocket:
    # Lazy import — matches `_shared.py::_init_tz_cache`'s convention: importing this module for
    # its class must stay cheap, and no real yfinance/network touch happens at import time.
    import yfinance as yf  # noqa: PLC0415

    # `yfinance` ships no type stubs (see `pyproject.toml`'s mypy `ignore_missing_imports`
    # override for `yfinance.*`) — this is the one narrow cast site isolating that 3rd-party
    # edge, matching `_shared.py::_get`'s own "one cast, not one per call site" convention.
    return cast("_LiveWebSocket", yf.live.AsyncWebSocket(verbose=False))


class YFinanceLiveQuoteManager:
    """One shared WebSocket connection, refcounted per symbol, with an idle-sweep unsubscribe.

    `ws_factory` exists purely for test injection (a fake `_LiveWebSocket`-shaped stub) — the
    real manager never touches the network until the first `subscribe()` call.
    """

    def __init__(self, ws_factory: Callable[[], _LiveWebSocket] = _default_ws_factory) -> None:
        self._ws_factory = ws_factory
        self._ws: _LiveWebSocket | None = None
        self._refcounts: dict[str, int] = {}
        self._last_access: dict[str, float] = {}
        self._latest: dict[str, dict[str, object]] = {}
        self._listen_task: asyncio.Task[None] | None = None
        self._sweep_task: asyncio.Task[None] | None = None

    def latest(self, yahoo_symbol: str) -> dict[str, object] | None:
        """Pure cache read, no I/O — also refreshes the idle-sweep clock for this symbol."""
        self._last_access[yahoo_symbol] = time.monotonic()
        return self._latest.get(yahoo_symbol)

    async def subscribe(self, yahoo_symbol: str) -> None:
        """Idempotent refcount increment. On 0→1, subscribes on the shared WS connection
        (lazily constructing it on first use) and ensures the listen/sweep loops are running."""
        self._last_access[yahoo_symbol] = time.monotonic()
        count = self._refcounts.get(yahoo_symbol, 0)
        self._refcounts[yahoo_symbol] = count + 1
        if count > 0:
            return
        ws = self._ensure_ws()
        await ws.subscribe([yahoo_symbol])
        self._ensure_listen_loop(ws)
        self._ensure_sweep_loop()

    async def unsubscribe(self, yahoo_symbol: str) -> None:
        """Decrement; at 0, unsubscribes on the shared connection and drops the cached tick —
        never serve a stale entry once nobody watches it."""
        count = self._refcounts.get(yahoo_symbol, 0) - 1
        if count > 0:
            self._refcounts[yahoo_symbol] = count
            return
        self._refcounts.pop(yahoo_symbol, None)
        self._last_access.pop(yahoo_symbol, None)
        self._latest.pop(yahoo_symbol, None)
        if self._ws is not None:
            await self._ws.unsubscribe([yahoo_symbol])

    def _ensure_ws(self) -> _LiveWebSocket:
        if self._ws is None:
            self._ws = self._ws_factory()
        return self._ws

    def _ensure_listen_loop(self, ws: _LiveWebSocket) -> None:
        if self._listen_task is None or self._listen_task.done():
            self._listen_task = asyncio.create_task(ws.listen(self._on_message))

    def _ensure_sweep_loop(self) -> None:
        if self._sweep_task is None or self._sweep_task.done():
            self._sweep_task = asyncio.create_task(self._sweep_idle())

    async def _on_message(self, decoded: dict[str, object]) -> None:
        """Single-writer (one listen task) — no lock needed for this plain dict write. Keyed by
        the decoded `PricingData` message's own `id` field."""
        symbol_id = decoded.get("id")
        if isinstance(symbol_id, str) and symbol_id:
            self._latest[symbol_id] = decoded

    async def _sweep_idle(self) -> None:
        """Unsubscribes any symbol nobody has called `latest()`/`subscribe()` on in a while —
        the mitigation for `get_live_price_quote` (a one-shot tool call) having no natural
        "done watching" disconnect signal, unlike the SSE `/quote/stream` path."""
        try:
            while self._refcounts:
                await asyncio.sleep(_IDLE_SWEEP_INTERVAL_SECONDS)
                now = time.monotonic()
                idle = [
                    symbol
                    for symbol, last in self._last_access.items()
                    if now - last >= _IDLE_UNSUBSCRIBE_AFTER_SECONDS
                ]
                for symbol in idle:
                    logger.info("yfinance_live_quote_idle_unsubscribe", symbol=symbol)
                    self._refcounts[symbol] = 1  # force the next unsubscribe to actually drop it
                    await self.unsubscribe(symbol)
        finally:
            self._sweep_task = None

    async def aclose(self) -> None:
        """Cancel the listen/sweep loops (suppress `CancelledError`), close the WS if
        constructed. Mirrors `QuotePoller.aclose()`."""
        for task in (self._listen_task, self._sweep_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._listen_task = None
        self._sweep_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None


__all__ = ["YFinanceLiveQuoteManager"]
