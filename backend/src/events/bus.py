"""The internal event bus: the source-of-truth pub/sub every lifecycle observer (journal
writer, SSE bridge, metrics, usage accounting) subscribes to. `Event` is the one envelope
shape every subscriber and the resumable-stream replay log share — nothing downstream ever
invents its own correlation fields.

This is a plain in-process async pub/sub, not a durable queue: `InMemoryEventBus` fans a
published `Event` out to every live subscriber via one `asyncio.Queue` per subscription.
Durable replay (reconnect after a dropped SSE connection) is not this module's job — that is
`stream_events` (a persistence sink a subscriber writes to), kept deliberately separate from
the live fan-out mechanism so the bus itself stays a pure, swappable Protocol.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class Event(BaseModel):
    """The correlation envelope every event carries, projected verbatim to the SSE frame and
    to the `stream_events` replay log. `seq` is assigned by the bus at publish time — callers
    never set it themselves, so it is always globally monotonic per turn regardless of how
    many producers (root agent, sub-agent lanes, hooks) publish concurrently."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    seq: int = Field(ge=0)
    ts: datetime
    turn_id: str | None = None
    agent_id: str | None = None
    parent_agent_id: str | None = None
    node_id: str | None = None
    data: dict[str, object] = Field(default_factory=dict)


@runtime_checkable
class EventBus(Protocol):
    """The pub/sub seam. `publish` is synchronous (fire-and-forget into subscriber queues);
    `subscribe` returns an async iterator a consumer drains until it stops listening."""

    def publish(  # noqa: PLR0913 — one field per correlation-envelope dimension
        self,
        kind: str,
        *,
        turn_id: str | None = None,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
        node_id: str | None = None,
        data: dict[str, object] | None = None,
    ) -> Event: ...

    def subscribe(self, kinds: list[str] | None = None) -> AsyncIterator[Event]: ...


class InMemoryEventBus:
    """The default `EventBus` implementation: one monotonic `seq` counter per turn (per the
    `UNIQUE(turn_id, event_seq)` invariant the replay log relies on) and an `asyncio.Queue`
    fan-out per subscriber.

    A `turn_id=None` event (no turn context yet, e.g. very early lifecycle events) gets its own
    counter under the `None` key — still monotonic, just not turn-scoped.
    """

    def __init__(self) -> None:
        self._subscribers: dict[int, tuple[list[str] | None, asyncio.Queue[Event]]] = {}
        self._next_subscriber_id = itertools.count()
        self._seq_counters: dict[str | None, int] = {}

    def _next_seq(self, turn_id: str | None) -> int:
        current = self._seq_counters.get(turn_id, -1) + 1
        self._seq_counters[turn_id] = current
        return current

    def publish(  # noqa: PLR0913 — mirrors EventBus's own shape above
        self,
        kind: str,
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
        return event

    async def subscribe(self, kinds: list[str] | None = None) -> AsyncIterator[Event]:
        """Yield every published event matching `kinds` (or every event, if `kinds` is
        `None`) until the caller stops iterating — cancelling the surrounding task is how a
        subscriber unsubscribes; there is no explicit `unsubscribe()` call."""
        subscriber_id = next(self._next_subscriber_id)
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[subscriber_id] = (kinds, queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.pop(subscriber_id, None)


__all__ = ["Event", "EventBus", "InMemoryEventBus"]
