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


# ---- Redis-backed demand leases (fakeredis) ------------------------------------------------


class _Clock:
    """A settable clock, so lease expiry is tested without waiting."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def _registry(redis: Redis, worker: str, clock: _Clock, *, cap: int = 100) -> RedisDemandRegistry:
    return RedisDemandRegistry(
        redis,
        key_prefix="demand",
        max_symbols=cap,
        lease_seconds=90.0,
        worker_id=worker,
        clock=clock,
    )


async def test_redis_demand_cap_is_fleet_wide_and_rejects_without_residue(
    fake_redis: Redis,
) -> None:
    clock = _Clock()
    a = _registry(fake_redis, "a", clock, cap=1)
    b = _registry(fake_redis, "b", clock, cap=1)
    assert await a.subscribe(Market.TW, "2330") is True
    assert await b.subscribe(Market.TW, "2330") is True  # the same symbol, still one distinct
    assert await b.subscribe(Market.TW, "2317") is False  # a second distinct symbol: over the cap

    members = {m.decode() for m in await fake_redis.zrange("demand:lease:TW", 0, -1)}
    assert members == {"2330|a", "2330|b"}  # the rejected symbol left nothing behind


async def test_a_workers_repeat_subscribers_and_unsubscribes_are_counted_locally(
    fake_redis: Redis,
) -> None:
    clock = _Clock()
    registry = _registry(fake_redis, "a", clock)
    await registry.subscribe(Market.TW, "2330")
    await registry.subscribe(Market.TW, "2330")  # a second tab on the same worker
    await registry.unsubscribe(Market.TW, "2330")
    assert await fake_redis.zcard("demand:lease:TW") == 1  # one tab left: the lease stays

    await registry.unsubscribe(Market.TW, "2330")
    assert await fake_redis.zcard("demand:lease:TW") == 0  # none left: the symbol leaves the set


async def test_a_killed_workers_claims_expire_and_stop_using_up_the_cap(fake_redis: Redis) -> None:
    """The shared refcount hash was only decremented by a clean disconnect, so every crash left its
    counts for good and they slowly used up the symbol cap for the whole fleet."""
    clock = _Clock()
    doomed = _registry(fake_redis, "doomed", clock, cap=3)
    survivor = _registry(fake_redis, "survivor", clock, cap=3)
    for symbol in ("2330", "2317", "2454"):
        assert await doomed.subscribe(Market.TW, symbol) is True  # then it is killed: no cleanup
    assert await survivor.subscribe(Market.TW, "2412") is False  # the cap is full of its claims

    clock.now += 91  # past the lease; the killed worker never renewed
    assert await survivor.subscribe(Market.TW, "2412") is True
    assert await survivor.subscribe(Market.TW, "2882") is True

    members = {m.decode() for m in await fake_redis.zrange("demand:lease:TW", 0, -1)}
    assert members == {"2412|survivor", "2882|survivor"}  # the dead worker's claims are gone


async def test_a_live_workers_claims_are_renewed_and_do_not_expire(fake_redis: Redis) -> None:
    clock = _Clock()
    live = _registry(fake_redis, "live", clock, cap=1)
    other = _registry(fake_redis, "other", clock, cap=1)
    await live.subscribe(Market.TW, "2330")

    for _ in range(5):  # five minutes, renewed every 30 s
        clock.now += 30
        await live.refresh()
    assert await other.subscribe(Market.TW, "2317") is False  # its claim is still there

    clock.now += 91  # it stops renewing (killed)
    assert await other.subscribe(Market.TW, "2317") is True


async def test_renewal_is_limited_to_a_third_of_a_lease(fake_redis: Redis) -> None:
    clock = _Clock()
    registry = _registry(fake_redis, "a", clock)
    await registry.subscribe(Market.TW, "2330")
    await registry.refresh()  # the first renewal always goes through
    renewed = await fake_redis.zscore("demand:lease:TW", "2330|a")

    clock.now += 10  # under a third of 90 s: no round trip
    await registry.refresh()
    assert await fake_redis.zscore("demand:lease:TW", "2330|a") == renewed

    clock.now += 25  # past it
    await registry.refresh()
    assert await fake_redis.zscore("demand:lease:TW", "2330|a") == renewed + 35


class _SpyRegistry(InMemoryDemandRegistry):
    def __init__(self, *, max_symbols: int, fail: bool = False) -> None:
        super().__init__(max_symbols=max_symbols)
        self.refreshes = 0
        self._fail = fail

    async def refresh(self) -> None:
        self.refreshes += 1
        if self._fail:
            raise RuntimeError("redis timed out")


async def test_the_poll_loop_renews_demand_and_survives_a_failed_renewal() -> None:
    for fail in (False, True):
        registry = _SpyRegistry(max_symbols=10, fail=fail)
        poller = QuotePoller(
            registry=registry,
            transport=InMemoryQuoteTransport(),
            fetcher=_CountingFetcher(),
            market_gate=_OpenGate(),
            channel_prefix="quote",
            cadence_seconds={"normal": 0.01, "saver": 0.02},
            auto_poll=True,
        )
        await poller.add_subscriber(Market.TW, "2330", "normal")
        for _ in range(300):  # a failing renewal logs a traceback, which can be slow
            if registry.refreshes >= 3:
                break
            await asyncio.sleep(0.01)

        assert registry.refreshes >= 3  # renewed on the loop's own cadence, failing or not
        assert poller._loop_task is not None and not poller._loop_task.done()
        await poller.aclose()


class _OneSymbolFails(_CountingFetcher):
    async def fetch(self, *, symbol: str, market: Market) -> QuoteFetchResult:
        if symbol == "2317":
            self.calls.append((market.value, symbol))
            raise RuntimeError("upstream timed out")
        return await super().fetch(symbol=symbol, market=market)


async def test_one_symbol_failing_does_not_stop_the_others_or_the_loop() -> None:
    """A fetch that raised used to end the poll task: every viewer on that worker stopped getting
    quotes until someone subscribed again."""
    fetcher = _OneSymbolFails()
    poller = _poller(fetcher, gate=_OpenGate(), registry=InMemoryDemandRegistry(max_symbols=100))
    for symbol in ("2317", "2330", "2454"):
        await poller.add_subscriber(Market.TW, symbol, "normal")

    fetched = await poller.poll_tick(clock=0.0)

    assert fetched == 2  # 2330 and 2454 were still fetched and published
    assert sorted(fetcher.calls) == [("TW", "2317"), ("TW", "2330"), ("TW", "2454")]
    assert await poller.poll_tick(clock=1.0) == 2  # the next tick runs normally
    await poller.aclose()
