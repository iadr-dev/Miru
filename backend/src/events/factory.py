"""Config-selected wiring for the event transport, so the interface layer never hardcodes a
backend. `create_app` builds one `EventBusFactory` from settings; routers ask it for a per-turn
bus (the producer side) and, for reconnect, a `StreamReader` (the consumer side).

Two backends behind one seam:

- **memory** — the single-process default. Each turn gets an `InMemoryEventBus`; there is no
  cross-worker reader (a reconnect can only live-attach to the in-process turn via the turn
  registry, exactly as before this transport existed), so `reader()` is `None`.
- **redis** — the multi-worker backend. Each turn gets a `RedisEventBus` that mirrors to the
  durable log + Redis channel; `reader()` returns a `StreamReader` that replays missed events
  from the durable log and then follows the live channel, so any worker can serve a reconnect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config.settings import Settings
from events.bus import Event, EventBus, InMemoryEventBus
from events.redis_bus import RedisEventBus, RedisStreamReader
from events.stream_store import SqlAlchemyStreamEventStore, StreamEventStore
from netclient.redis_clients import request_path_redis, subscription_redis


@runtime_checkable
class StreamReader(Protocol):
    """The reconnect consumer seam: durable replay + live follow, composed so a resume is gapless
    and duplicate-free (see `redis_bus` for the boundary reasoning)."""

    async def replay_since(self, turn_id: str, after_seq: int) -> list[Event]: ...

    def live(self, turn_id: str) -> AsyncIterator[Event]: ...


@runtime_checkable
class EventBusFactory(Protocol):
    """Produces a per-turn `EventBus` and (when the backend supports cross-worker reconnect) a
    `StreamReader`. A `None` reader means "no durable/cross-worker path — live-attach only"."""

    def new_bus(self, *, turn_id: str, user_id: str | None, thread_id: str | None) -> EventBus: ...

    def reader(self) -> StreamReader | None: ...


class InMemoryEventBusFactory:
    """The single-process default: a fresh in-process bus per turn, no cross-worker reader."""

    def new_bus(self, *, turn_id: str, user_id: str | None, thread_id: str | None) -> EventBus:
        del turn_id, user_id, thread_id
        return InMemoryEventBus()

    def reader(self) -> StreamReader | None:
        return None


class _RedisStreamReader:
    """Binds the durable store's `replay_since` and the Redis channel's `live` into one
    `StreamReader` the router can compose in the correct order."""

    def __init__(self, store: StreamEventStore, live_reader: RedisStreamReader) -> None:
        self._store = store
        self._live = live_reader

    async def replay_since(self, turn_id: str, after_seq: int) -> list[Event]:
        return await self._store.replay_since(turn_id, after_seq)

    def live(self, turn_id: str) -> AsyncIterator[Event]:
        return self._live.live(turn_id)


class RedisEventBusFactory:
    """The multi-worker backend: Redis-mirrored per-turn buses + a durable-replay-then-live
    reader. The `Redis` client and DB session factory are injected so tests can pass a fake
    Redis and an in-memory SQLite factory without a live service."""

    def __init__(
        self,
        redis: Redis,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        commands: Redis | None = None,
    ) -> None:
        # `redis` carries the subscriptions; a turn's publishes go through `commands`.
        self._redis = commands or redis
        self._store: StreamEventStore = SqlAlchemyStreamEventStore(session_factory)
        self._live_reader = RedisStreamReader(redis)

    def new_bus(self, *, turn_id: str, user_id: str | None, thread_id: str | None) -> EventBus:
        return RedisEventBus(
            self._redis,
            turn_id=turn_id,
            user_id=user_id,
            thread_id=thread_id,
            stream_store=self._store,
        )

    def reader(self) -> StreamReader | None:
        return _RedisStreamReader(self._store, self._live_reader)


def build_event_bus_factory(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> EventBusFactory:
    """Select the transport backend from config — never hardcoded at the call site."""
    if settings.event_bus_backend == "redis":
        return RedisEventBusFactory(
            subscription_redis(settings), session_factory, commands=request_path_redis(settings)
        )
    return InMemoryEventBusFactory()


__all__ = [
    "EventBusFactory",
    "InMemoryEventBusFactory",
    "RedisEventBusFactory",
    "StreamReader",
    "build_event_bus_factory",
]
