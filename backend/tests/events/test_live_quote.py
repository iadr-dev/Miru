"""Unit tests for the shared live-quote poller, demand registry, and fan-out — the mechanics the
`/quote/stream` route composes. The real-route sharing/cleanup proof lives in
tests/api/test_quote_stream_route.py; here we exercise the primitives directly."""

from __future__ import annotations

import asyncio
from datetime import datetime

from redis.asyncio import Redis

from events.live_quote import (
    InMemoryDemandRegistry,
    InMemoryQuoteTransport,
    QuoteFetchResult,
    QuotePoller,
    RedisDemandRegistry,
    channel_for,
)
from schemas.enums import Market

_CADENCE = {"normal": 0.01, "saver": 0.04}


class _CountingFetcher:
    """Records every fetch so a test can prove one poll → N clients (not N fetches)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, *, symbol: str, market: Market) -> QuoteFetchResult:
        self.calls.append((market.value, symbol))
        return QuoteFetchResult(
            quote={"bar": {"trade_date": "2024-01-02", "close": 101.0}}, as_of="2024-01-02"
        )


class _OpenGate:
    def __init__(self, *, is_open: bool = True) -> None:
        self._open = is_open

    def is_open(self, market: Market, now: datetime) -> bool:
        del market, now
        return self._open


def _poller(fetcher: object, *, gate: object, registry: object) -> QuotePoller:
    # auto_poll off: these tests drive `poll_tick` by hand, so the background timed loop must not
    # interleave its own ticks with (and perturb the cadence-due window of) the manual ones.
    return QuotePoller(
        registry=registry,  # type: ignore[arg-type]
        transport=InMemoryQuoteTransport(),
        fetcher=fetcher,  # type: ignore[arg-type]
        market_gate=gate,  # type: ignore[arg-type]
        channel_prefix="quote",
        cadence_seconds=_CADENCE,
        auto_poll=False,
    )


async def test_one_poll_serves_many_local_subscribers() -> None:
    """Two local subscribers to the same symbol → the tick fetches it exactly once."""
    fetcher = _CountingFetcher()
    poller = _poller(fetcher, gate=_OpenGate(), registry=InMemoryDemandRegistry(max_symbols=100))
    await poller.add_subscriber(Market.TW, "2330", "normal")
    await poller.add_subscriber(Market.TW, "2330", "normal")

    fetched = await poller.poll_tick()

    assert fetched == 1
    assert fetcher.calls == [("TW", "2330")]
    await poller.aclose()


async def test_market_closed_symbol_is_not_fetched() -> None:
    fetcher = _CountingFetcher()
    poller = _poller(
        fetcher, gate=_OpenGate(is_open=False), registry=InMemoryDemandRegistry(max_symbols=100)
    )
    await poller.add_subscriber(Market.TW, "2330", "normal")

    assert await poller.poll_tick() == 0
    assert fetcher.calls == []
    await poller.aclose()


async def test_last_disconnect_stops_touching_symbol() -> None:
    """After the last subscriber leaves, the symbol is out of local demand → never fetched."""
    fetcher = _CountingFetcher()
    registry = InMemoryDemandRegistry(max_symbols=100)
    poller = _poller(fetcher, gate=_OpenGate(), registry=registry)
    await poller.add_subscriber(Market.TW, "2330", "normal")
    await poller.remove_subscriber(Market.TW, "2330", "normal")

    assert await poller.poll_tick() == 0
    assert fetcher.calls == []
    await poller.aclose()


async def test_cadence_tie_break_serves_fastest_requested() -> None:
    """A symbol with one `saver` and one `normal` subscriber polls at `normal`'s faster rate, so
    the `normal` subscriber is never starved."""
    fetcher = _CountingFetcher()
    poller = _poller(fetcher, gate=_OpenGate(), registry=InMemoryDemandRegistry(max_symbols=100))
    await poller.add_subscriber(Market.TW, "2330", "saver")
    await poller.add_subscriber(Market.TW, "2330", "normal")

    assert await poller.poll_tick(clock=100.0) == 1  # first poll fires for both
    # A second tick at +0.02s: due only if the interval is `normal` (0.01s), not `saver` (0.04s).
    assert await poller.poll_tick(clock=100.02) == 1  # normal's cadence made it due again
    assert len(fetcher.calls) == 2
    await poller.aclose()


async def test_saver_only_symbol_uses_slower_interval() -> None:
    fetcher = _CountingFetcher()
    poller = _poller(fetcher, gate=_OpenGate(), registry=InMemoryDemandRegistry(max_symbols=100))
    await poller.add_subscriber(Market.TW, "2330", "saver")

    assert await poller.poll_tick(clock=100.0) == 1
    assert await poller.poll_tick(clock=100.02) == 0  # past normal's interval, under saver's
    await poller.aclose()


async def test_global_demand_cap_rejects_over_cap_symbol() -> None:
    fetcher = _CountingFetcher()
    poller = _poller(fetcher, gate=_OpenGate(), registry=InMemoryDemandRegistry(max_symbols=1))
    assert await poller.add_subscriber(Market.TW, "2330", "normal") is True
    assert await poller.add_subscriber(Market.TW, "2317", "normal") is False  # over the cap
    await poller.aclose()


async def test_published_frame_carries_as_of_and_market_open() -> None:
    fetcher = _CountingFetcher()
    transport = InMemoryQuoteTransport()
    poller = QuotePoller(
        registry=InMemoryDemandRegistry(max_symbols=100),
        transport=transport,
        fetcher=fetcher,
        market_gate=_OpenGate(),
        channel_prefix="quote",
        cadence_seconds=_CADENCE,
        auto_poll=False,
    )
    await poller.add_subscriber(Market.TW, "2330", "normal")

    received: list[dict[str, object]] = []
    ready = asyncio.Event()

    async def _consume() -> None:
        agen = transport.subscribe([channel_for(Market.TW, "2330", prefix="quote")])
        ready.set()
        async for event in agen:
            received.append(event.data)
            return

    task = asyncio.create_task(_consume())
    await ready.wait()
    await asyncio.sleep(0)
    await poller.poll_tick()
    await asyncio.wait_for(task, timeout=2)

    assert received[0]["as_of"] == "2024-01-02"
    assert received[0]["market_open"] is True
    await poller.aclose()


# ---- Redis-backed demand refcount (fakeredis) ----------------------------------------------


async def test_redis_demand_refcount_atomic_and_reject_over_cap(fake_redis: Redis) -> None:
    registry = RedisDemandRegistry(fake_redis, key_prefix="demand", max_symbols=1)
    assert await registry.subscribe(Market.TW, "2330") is True
    assert await registry.subscribe(Market.TW, "2330") is True  # same symbol, still under cap
    assert await registry.subscribe(Market.TW, "2317") is False  # new symbol over the cap

    # The rejected symbol left no residue (the reserve-before-act undo ran).
    assert await fake_redis.hexists("demand:TW", "2317") == 0
    assert int(await fake_redis.hget("demand:TW", "2330")) == 2

    await registry.unsubscribe(Market.TW, "2330")
    assert int(await fake_redis.hget("demand:TW", "2330")) == 1
    await registry.unsubscribe(Market.TW, "2330")
    assert await fake_redis.hexists("demand:TW", "2330") == 0  # zero → symbol leaves the demand set
