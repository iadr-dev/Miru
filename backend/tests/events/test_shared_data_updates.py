"""`data.updated` reaches every open tab through one subscription per process, and a backfill
publishes one signal per dataset instead of one per partition.

Before: each open `/updates/stream` held its own Redis pub/sub subscription, which holds its own
pooled connection, so the 101st tab on a process found the pool (100 by default) empty and its
stream ended silently, and the browser reconnected every few seconds. A three-year backfill
published about 11,000 signals, each delivered to every open tab and to the alert engine."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis

from api.routers import context as context_routes
from config.settings import Settings
from events.bus import Event
from events.data_updates import SharedDataUpdateFeed
from events.kinds import EventKind
from events.live_quote_factory import build_data_update_broker
from ingest.data_updates import CoalescingDataUpdatePublisher
from schemas.enums import Capability, Market


def _event(as_of: str) -> Event:
    return Event(
        kind=EventKind.DATA_UPDATED,
        seq=0,
        ts=datetime.now(UTC),
        data={"capability": "price_eod", "market": "TW", "as_of": as_of, "scope": "whole-market"},
    )


class _Source:
    """A scripted upstream subscription: counts how often it is opened and is fed by the test."""

    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0
        self.queue: asyncio.Queue[Event | Exception] = asyncio.Queue()

    async def updates(self) -> AsyncIterator[Event]:
        self.opened += 1
        try:
            while True:
                item = await self.queue.get()
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            self.closed += 1


async def _take(stream: AsyncIterator[Event], n: int) -> list[Event]:
    return [await anext(stream) for _ in range(n)]


async def test_every_stream_gets_every_signal_through_one_subscription() -> None:
    source = _Source()
    feed = SharedDataUpdateFeed(source.updates)
    streams = [feed.updates() for _ in range(50)]
    readers = [asyncio.create_task(_take(s, 2)) for s in streams]
    await asyncio.sleep(0.05)

    source.queue.put_nowait(_event("2026-10-13"))
    source.queue.put_nowait(_event("2026-10-14"))
    got = await asyncio.wait_for(asyncio.gather(*readers), timeout=2)

    assert source.opened == 1
    assert all([e.data["as_of"] for e in g] == ["2026-10-13", "2026-10-14"] for g in got)
    for stream in streams:
        await stream.aclose()  # type: ignore[attr-defined]
    assert feed.subscribers == 0
    assert source.closed == 1  # the last one out closes the subscription


async def test_a_failing_subscription_is_reopened_and_the_streams_stay_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    source = _Source()
    feed = SharedDataUpdateFeed(source.updates)
    stream = feed.updates()
    reader = asyncio.create_task(_take(stream, 1))
    await _real_sleep(0.02)

    source.queue.put_nowait(ConnectionError("redis restarted"))
    await _real_sleep(0.05)
    source.queue.put_nowait(_event("2026-10-14"))

    [event] = await asyncio.wait_for(reader, timeout=2)
    assert event.data["as_of"] == "2026-10-14"
    assert source.opened == 2  # resubscribed once
    await stream.aclose()  # type: ignore[attr-defined]


async def test_a_reader_that_stops_reading_loses_its_oldest_not_memory() -> None:
    source = _Source()
    feed = SharedDataUpdateFeed(source.updates)
    stuck = feed.updates()
    first = asyncio.create_task(anext(stuck))  # subscribes, then reads nothing more
    await asyncio.sleep(0.02)
    total = SharedDataUpdateFeed._QUEUE_SIZE + 10

    for n in range(total):
        source.queue.put_nowait(_event(f"d{n:04d}"))
    await asyncio.sleep(0.1)

    received = [await first]
    with contextlib.suppress(TimeoutError):
        while True:
            received.append(await asyncio.wait_for(anext(stuck), timeout=0.1))
    days = [e.data["as_of"] for e in received]
    assert days[-1] == f"d{total - 1:04d}"  # the newest is never the one dropped
    assert len(days) <= SharedDataUpdateFeed._QUEUE_SIZE + 1
    assert len(days) < total  # the oldest were dropped
    await stuck.aclose()  # type: ignore[attr-defined]


_real_sleep = asyncio.sleep


async def _fast_sleep(delay: float, *args: object) -> None:
    await _real_sleep(min(delay, 0.01), *args)


# ---- the backfill publishes one signal per dataset, at its newest date --------------------------


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[Capability, Market, str, str]] = []

    async def publish_data_updated(
        self, *, capability: Capability, market: Market, as_of: str, scope: str
    ) -> None:
        self.sent.append((capability, market, as_of, scope))


async def test_a_backfill_publishes_each_dataset_once_at_its_newest_date() -> None:
    recorder = _Recorder()
    signals = CoalescingDataUpdatePublisher(recorder)
    for day in range(1, 29):
        for capability in (Capability.PRICE_EOD, Capability.INSTITUTIONAL_FLOW):
            await signals.publish_data_updated(
                capability=capability,
                market=Market.TW,
                as_of=f"2026-02-{day:02d}",
                scope="whole-market",
            )
    await signals.publish_data_updated(
        capability=Capability.PRICE_EOD, market=Market.US, as_of="2026-02-27", scope="whole-market"
    )
    assert recorder.sent == []  # nothing goes out while the backfill runs

    assert await signals.flush() == 3
    assert sorted(recorder.sent) == sorted(
        [
            (Capability.PRICE_EOD, Market.TW, "2026-02-28", "whole-market"),
            (Capability.INSTITUTIONAL_FLOW, Market.TW, "2026-02-28", "whole-market"),
            (Capability.PRICE_EOD, Market.US, "2026-02-27", "whole-market"),
        ]
    )
    assert await signals.flush() == 0  # flushed once


# ---- the SSE relay: reconnect delay first, keep-alive while idle --------------------------------


async def test_the_stream_sets_a_reconnect_delay_and_keeps_an_idle_connection_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_routes, "_KEEPALIVE_SECONDS", 0.05)
    source = _Source()
    relay = context_routes._with_keepalive(source.updates())

    assert await anext(relay) == "retry: 10000\n\n"
    assert await asyncio.wait_for(anext(relay), timeout=1) == ": keep-alive\n\n"
    source.queue.put_nowait(_event("2026-10-14"))
    frame = await asyncio.wait_for(anext(relay), timeout=1)
    while frame.startswith(":"):
        frame = await asyncio.wait_for(anext(relay), timeout=1)
    assert "2026-10-14" in frame
    await relay.aclose()  # type: ignore[attr-defined]
    assert source.closed == 1


# ---- real Redis: many tabs, one connection ------------------------------------------------------

_REDIS_URL = os.environ.get("TEST_REDIS_URL")


@pytest.mark.concurrency
async def test_more_open_streams_than_the_redis_pool_holds_all_get_the_signal() -> None:
    if not _REDIS_URL:
        pytest.skip("TEST_REDIS_URL not set - real Redis check skipped")
    pool_size = 10
    client = Redis.from_url(_REDIS_URL, max_connections=pool_size)
    channel = f"data.updated.test.{os.getpid()}"
    broker = build_data_update_broker(
        Settings(event_bus_backend="redis", data_updated_key_prefix=channel), redis=client
    )
    streams = [broker.reader_factory.updates() for _ in range(pool_size * 4)]
    readers = [asyncio.create_task(anext(s)) for s in streams]
    try:
        await asyncio.sleep(0.5)  # the one subscription is in place
        await broker.publisher.publish_data_updated(
            capability=Capability.PRICE_EOD,
            market=Market.TW,
            as_of="2026-10-14",
            scope="whole-market",
        )
        got = await asyncio.wait_for(asyncio.gather(*readers), timeout=5)
        assert all(e.data["as_of"] == "2026-10-14" for e in got)
        subscribers = (await client.pubsub_numsub(channel))[0][1]
        assert subscribers == 1  # one subscription for all forty streams
    finally:
        for stream in streams:
            with contextlib.suppress(Exception):
                await stream.aclose()  # type: ignore[attr-defined]
        await client.aclose()
