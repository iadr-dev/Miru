"""The config-selected event-bus factory: `memory` vs `redis` backend selection and the
reader-availability contract (memory has no cross-worker reader; redis does)."""

from __future__ import annotations

import fakeredis.aioredis
from sqlalchemy.ext.asyncio import async_sessionmaker

from config.settings import Settings
from events.bus import InMemoryEventBus
from events.factory import (
    InMemoryEventBusFactory,
    RedisEventBusFactory,
    build_event_bus_factory,
)
from events.redis_bus import RedisEventBus


def test_memory_backend_selected_by_default(session_factory: async_sessionmaker) -> None:
    factory = build_event_bus_factory(Settings(event_bus_backend="memory"), session_factory)
    assert isinstance(factory, InMemoryEventBusFactory)
    bus = factory.new_bus(turn_id="t1", user_id="u1", thread_id="th1")
    assert isinstance(bus, InMemoryEventBus)
    assert factory.reader() is None  # no durable/cross-worker path on the in-memory backend


async def test_redis_backend_produces_redis_bus_and_reader(
    session_factory: async_sessionmaker,
) -> None:
    redis = fakeredis.aioredis.FakeRedis()
    factory = RedisEventBusFactory(redis, session_factory)
    bus = factory.new_bus(turn_id="t1", user_id="u1", thread_id="th1")
    assert isinstance(bus, RedisEventBus)
    assert factory.reader() is not None  # redis backend supports durable cross-worker resume
    await redis.aclose()
