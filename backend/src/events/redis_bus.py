"""The cross-process `EventBus` implementation: same-worker live fan-out plus a Redis Pub/Sub +
durable-log mirror so any *other* worker can attach to, or replay, a turn it did not start.

Why this is not a layering violation of "only `sources/` performs network I/O": that rule governs
**market-data acquisition** — a caller names a capability + `(symbol, market)` and the router
resolves a source, so no request-path layer can smuggle in an undeclared data provider or bypass
governance/entitlement on the fetched data. This bus opens no data source and fetches no market
data; it is the *transport* for the interaction layer's own lifecycle events (`turn.start`,
`agent.spawned`, `message`, `turn.end`). Redis here is infrastructure plumbing for events this
layer already owns end-to-end in-process — the same category as the outbound HTTP the OAuth and
Paddle builds make for auth/billing, which are likewise not source-data fetches and likewise do
not sit behind the `sources/` seam. The `sources/` DuckDB/Redis *caching adapter* is a different
concern entirely (caching fetched source payloads); event transport is not cached source data.

Design (matches the hosted-topology contract that the in-process bus stays a same-worker
optimization while Redis is the cross-worker source of truth for the live path):

- `subscribe()` returns the **local, in-process** fan-out. The worker that runs the turn also
  serves that turn's own SSE response, and an in-process queue is race-free — there is no
  subscribe-before-publish gap the way a raw Pub/Sub subscription would have.
- `publish()` fans out locally **and** mirrors each event to the durable `stream_events` log and
  the `stream:{turn_id}` Redis channel, so a reconnect that lands on a *different* worker can
  replay what it missed (from the log) and then follow live (from the channel) — that
  cross-worker read path is `RedisStreamReader`, not this bus's own `subscribe`.

The local `seq` counter is authoritative because there is exactly one producing worker per turn.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import structlog
from redis.asyncio import Redis

from events.bus import Event
from events.kinds import EventKind
from events.stream_store import StreamEventStore

logger = structlog.get_logger(__name__)

_CHANNEL_PREFIX = "stream:"


def _encode(event: Event) -> str:
    return json.dumps(event.model_dump(mode="json"), ensure_ascii=False)


def _decode(raw: bytes | str) -> Event:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    return Event.model_validate(json.loads(text))


def channel_for(turn_id: str | None, *, prefix: str = _CHANNEL_PREFIX) -> str:
    return f"{prefix}{turn_id}"


class RedisEventBus:
    """`EventBus` whose `publish` mirrors to Redis + the durable log for cross-worker consumers,
    while `subscribe` serves the local worker's own stream in-process (race-free). Durable
    persistence and the Redis publish are best-effort relative to the local fan-out: a transport
    hiccup is logged and never blocks the turn or the local subscriber."""

    def __init__(
        self,
        redis: Redis,
        *,
        turn_id: str | None = None,
        user_id: str | None = None,
        stream_store: StreamEventStore | None = None,
        channel_prefix: str = _CHANNEL_PREFIX,
    ) -> None:
        self._redis = redis
        self._turn_id = turn_id
        self._user_id = user_id
        self._store = stream_store
        self._channel_prefix = channel_prefix
        self._seq_counters: dict[str | None, int] = {}
        self._subscribers: dict[int, tuple[list[EventKind] | None, asyncio.Queue[Event]]] = {}
        self._next_subscriber_id = itertools.count()
        self._outbox: asyncio.Queue[Event] = asyncio.Queue()
        self._writer_task: asyncio.Task[None] | None = None

    def _next_seq(self, turn_id: str | None) -> int:
        current = self._seq_counters.get(turn_id, -1) + 1
        self._seq_counters[turn_id] = current
        return current

    def publish(  # noqa: PLR0913 — mirrors EventBus's own shape
        self,
        kind: EventKind,
        *,
        turn_id: str | None = None,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
        node_id: str | None = None,
        data: dict[str, object] | None = None,
    ) -> Event:
        event = Event(
            kind=kind,
            seq=self._next_seq(turn_id),
            ts=datetime.now(UTC),
            turn_id=turn_id,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
            node_id=node_id,
            data=data or {},
        )
        for kinds_filter, queue in self._subscribers.values():
            if kinds_filter is None or kind in kinds_filter:
                queue.put_nowait(event)
        self._ensure_writer()
        self._outbox.put_nowait(event)
        return event

    async def subscribe(self, kinds: list[EventKind] | None = None) -> AsyncIterator[Event]:
        """The local, in-process fan-out for the worker running this turn — one queue per
        subscriber, drained until the caller stops iterating (cancelling the task unsubscribes).
        Cross-worker consumers do not use this; they use `RedisStreamReader`."""
        subscriber_id = next(self._next_subscriber_id)
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[subscriber_id] = (kinds, queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.pop(subscriber_id, None)

    def _ensure_writer(self) -> None:
        if self._writer_task is None or self._writer_task.done():
            self._writer_task = asyncio.create_task(self._drain_outbox())

    async def _drain_outbox(self) -> None:
        while True:
            event = await self._outbox.get()
            try:
                # Durable-append before the wire publish, so a reconnecting worker can never see a
                # live frame that is not yet findable in the replay log.
                if self._store is not None:
                    await self._store.append(event, user_id=self._user_id)
                await self._redis.publish(
                    channel_for(event.turn_id, prefix=self._channel_prefix), _encode(event)
                )
            except Exception:  # a transport hiccup must not kill the drainer for the whole turn
                logger.warning("redis_event_publish_failed", seq=event.seq)
            finally:
                self._outbox.task_done()

    async def aclose(self) -> None:
        """Drain queued mirrors, then stop the background writer — called when the turn's stream
        ends so the per-turn bus leaves no dangling task."""
        await self._outbox.join()
        if self._writer_task is not None:
            self._writer_task.cancel()
            self._writer_task = None


class RedisStreamReader:
    """The cross-worker read path for `GET /threads/{id}/stream`: subscribe to the turn's Redis
    channel first, then follow live. It intentionally does not touch the durable log itself — the
    router composes replay (from `StreamEventStore`) ahead of this so the ordering is
    subscribe-live → replay-missed → emit-live-filtered, which is what makes the resume gapless
    and duplicate-free even under the Pub/Sub cold-start window."""

    def __init__(self, redis: Redis, *, channel_prefix: str = _CHANNEL_PREFIX) -> None:
        self._redis = redis
        self._channel_prefix = channel_prefix

    async def live(self, turn_id: str) -> AsyncIterator[Event]:
        pubsub = self._redis.pubsub()
        channel = channel_for(turn_id, prefix=self._channel_prefix)
        await pubsub.subscribe(channel)
        try:
            # redis-py types `listen()` as a bare, unparametrized `AsyncIterator`, so narrow the
            # message to the known Pub/Sub envelope shape at this boundary rather than letting an
            # unknown type leak into the event decode.
            messages = cast("AsyncIterator[dict[str, object]]", pubsub.listen())
            async for message in messages:
                if message.get("type") != "message":
                    continue  # skip the subscribe/unsubscribe confirmations
                data = message["data"]
                if isinstance(data, bytes | str):
                    yield _decode(data)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py PubSub.aclose lacks a stub annotation


__all__ = ["RedisEventBus", "RedisStreamReader", "channel_for"]
