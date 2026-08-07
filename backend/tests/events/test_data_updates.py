"""The `data.updated` invalidation transport: in-process broker + the Redis publisher/reader
pair over fakeredis. The end-to-end proof that the ingest batch-completion path actually reaches
`/updates/stream` lives in tests/api and tests/ingest; here we prove the transport primitives."""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis

from events.data_updates import (
    InMemoryDataUpdateBroker,
    RedisDataUpdatePublisher,
    RedisDataUpdateReader,
)
from schemas.enums import Capability, Market


async def test_in_memory_broker_publish_reaches_reader() -> None:
    broker = InMemoryDataUpdateBroker()
    received: list[dict[str, object]] = []
    ready = asyncio.Event()

    async def _consume() -> None:
        agen = broker.updates()
        ready.set()
        async for event in agen:
            received.append(event.data)
            return

    task = asyncio.create_task(_consume())
    await ready.wait()
    await asyncio.sleep(0)
    await broker.publish_data_updated(
        capability=Capability.PRICE_EOD, market=Market.TW, as_of="2024-01-02", scope="whole-market"
    )
    await asyncio.wait_for(task, timeout=2)

    assert received[0] == {
        "capability": "price_eod",
        "market": "TW",
        "as_of": "2024-01-02",
        "scope": "whole-market",
    }


async def test_redis_publish_reaches_cross_process_reader(fake_redis: Redis) -> None:
    publisher = RedisDataUpdatePublisher(fake_redis, channel="data.updated")
    reader = RedisDataUpdateReader(fake_redis, channel="data.updated")
    received: list[dict[str, object]] = []
    ready = asyncio.Event()

    async def _consume() -> None:
        agen = reader.updates()
        ready.set()
        async for event in agen:
            received.append(event.data)
            return

    task = asyncio.create_task(_consume())
    await ready.wait()
    await asyncio.sleep(0.05)  # let pubsub.subscribe() land before publishing
    await publisher.publish_data_updated(
        capability=Capability.INSTITUTIONAL_FLOW,
        market=Market.TW,
        as_of="2024-01-02",
        scope="whole-market",
    )
    await asyncio.wait_for(task, timeout=2)

    assert received[0]["capability"] == "institutional_flow"
    assert received[0]["scope"] == "whole-market"
