"""Unit tests for `YFinanceLiveQuoteManager` — the yfinance WebSocket live-quote singleton.
`ws_factory` injects a fake `_LiveWebSocket`-shaped stub so no real network/websocket connects.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sources.yfinance.live_quote_stream import YFinanceLiveQuoteManager


class _FakeWebSocket:
    """Records subscribe/unsubscribe/close calls; `listen()` just parks forever (like the real
    `AsyncWebSocket.listen()`'s inner loop) until cancelled, so `aclose()` exercises real
    cancel-and-await behavior."""

    def __init__(self) -> None:
        self.subscribed: list[list[str]] = []
        self.unsubscribed: list[list[str]] = []
        self.closed = False
        self.listen_calls = 0

    async def subscribe(self, symbols: list[str]) -> None:
        self.subscribed.append(symbols)

    async def unsubscribe(self, symbols: list[str]) -> None:
        self.unsubscribed.append(symbols)

    async def listen(self, message_handler: Callable[[dict[str, object]], Awaitable[None]]) -> None:
        del message_handler
        self.listen_calls += 1
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True


def _manager() -> tuple[YFinanceLiveQuoteManager, _FakeWebSocket]:
    ws = _FakeWebSocket()
    manager = YFinanceLiveQuoteManager(ws_factory=lambda: ws)
    return manager, ws


async def test_subscribe_twice_shares_one_ws_subscription() -> None:
    """One WS connection → N subscribers: a second subscribe to the same symbol must not
    re-subscribe on the wire, mirroring `QuotePoller`'s own "one poll serves many local
    subscribers" proof."""
    manager, ws = _manager()
    await manager.subscribe("AAPL")
    await manager.subscribe("AAPL")
    await asyncio.sleep(0)  # let the scheduled listen task actually start running
    assert ws.subscribed == [["AAPL"]]
    assert ws.listen_calls == 1
    await manager.aclose()


async def test_unsubscribe_at_zero_refcount_drops_cache_and_unsubscribes() -> None:
    manager, ws = _manager()
    await manager.subscribe("AAPL")
    await manager._on_message({"id": "AAPL", "price": 230.5})
    assert manager.latest("AAPL") == {"id": "AAPL", "price": 230.5}

    await manager.subscribe("AAPL")  # second subscriber
    await manager.unsubscribe("AAPL")  # first leaves — still one subscriber
    assert ws.unsubscribed == []
    assert manager.latest("AAPL") is not None

    await manager.unsubscribe("AAPL")  # last leaves — actually unsubscribes
    assert ws.unsubscribed == [["AAPL"]]
    assert manager.latest("AAPL") is None
    await manager.aclose()


async def test_on_message_keys_by_decoded_id_field() -> None:
    manager, _ws = _manager()
    await manager.subscribe("AAPL")
    await manager.subscribe("MSFT")
    await manager._on_message({"id": "AAPL", "price": 230.5})
    await manager._on_message({"id": "MSFT", "price": 410.0})
    assert manager.latest("AAPL") == {"id": "AAPL", "price": 230.5}
    assert manager.latest("MSFT") == {"id": "MSFT", "price": 410.0}
    await manager.aclose()


async def test_aclose_cancels_listen_task_and_closes_ws() -> None:
    manager, ws = _manager()
    await manager.subscribe("AAPL")
    assert manager._listen_task is not None
    await manager.aclose()
    assert manager._listen_task is None
    assert ws.closed is True


async def test_aclose_without_ever_subscribing_is_a_no_op() -> None:
    manager, ws = _manager()
    await manager.aclose()
    assert ws.closed is False  # never constructed — `_ws` stayed `None`
