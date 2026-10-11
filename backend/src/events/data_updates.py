"""Cross-process backend for the ingest leader's `data.updated` invalidation signal.

`ingest/data_updates.py` defines the `DataUpdatePublisher` seam (in the ingest layer, so the
write path can depend on it without an upward import). This module supplies the concrete
transport that satisfies it structurally and the reader the API worker's `/updates/stream` relays
from — the same split, and the same layering justification, as the event bus: this is transport
plumbing for a signal the interaction layer owns, not a market-data fetch behind the `sources/`
seam.

Two backends behind one config selection, mirroring `event_bus_backend`:

- **redis** — the leader publishes to a single `data.updated` channel; any API worker subscribes
  and relays to its connected clients (the batch lands on the scheduler/leader process, the SSE
  connection lives on an arbitrary API worker, so the signal must cross processes).
- **memory** — single-process default: one shared in-process bus both sides use.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import structlog
from redis.asyncio import Redis

from events.bus import Event, InMemoryEventBus
from events.kinds import EventKind
from schemas.enums import Capability, Market

logger = structlog.get_logger(__name__)


def _payload(capability: Capability, market: Market, as_of: str, scope: str) -> dict[str, object]:
    return {
        "capability": capability.value,
        "market": market.value,
        "as_of": as_of,
        "scope": scope,
    }


class RedisDataUpdatePublisher:
    """Publishes `data.updated` onto a Redis channel — the leader-side half. Best-effort relative
    to the write that triggered it: a transport hiccup is logged, never a reason to fail an ingest
    run whose partition already landed."""

    def __init__(self, redis: Redis, *, channel: str) -> None:
        self._redis = redis
        self._channel = channel

    async def publish_data_updated(
        self, *, capability: Capability, market: Market, as_of: str, scope: str
    ) -> None:
        event = Event(
            kind=EventKind.DATA_UPDATED,
            seq=0,
            ts=datetime.now(UTC),
            data=_payload(capability, market, as_of, scope),
        )
        try:
            await self._redis.publish(
                self._channel, json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
            )
        except Exception:  # a transport hiccup must not fail the already-landed batch
            logger.warning("data_updated_publish_failed", capability=capability.value)


class RedisDataUpdateReader:
    """The API-worker-side half: subscribe to the `data.updated` channel and yield each signal as
    an `Event` for the SSE relay."""

    def __init__(self, redis: Redis, *, channel: str) -> None:
        self._redis = redis
        self._channel = channel

    async def updates(self) -> AsyncIterator[Event]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._channel)
        try:
            messages = cast("AsyncIterator[dict[str, object]]", pubsub.listen())
            async for message in messages:
                if message.get("type") != "message":
                    continue
                data = message["data"]
                if isinstance(data, bytes | str):
                    text = data.decode("utf-8") if isinstance(data, bytes) else data
                    yield Event.model_validate(json.loads(text))
        finally:
            await pubsub.unsubscribe(self._channel)
            await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py PubSub.aclose lacks a stub


class InMemoryDataUpdateBroker:
    """Single-process publisher + reader over one shared in-process bus — the default backend,
    and the seam a same-process test drives end to end (leader publish → worker relay)."""

    def __init__(self) -> None:
        self._bus = InMemoryEventBus()

    async def publish_data_updated(
        self, *, capability: Capability, market: Market, as_of: str, scope: str
    ) -> None:
        self._bus.publish(EventKind.DATA_UPDATED, data=_payload(capability, market, as_of, scope))

    def updates(self) -> AsyncIterator[Event]:
        return self._bus.subscribe([EventKind.DATA_UPDATED])


__all__ = [
    "InMemoryDataUpdateBroker",
    "RedisDataUpdatePublisher",
    "RedisDataUpdateReader",
]
