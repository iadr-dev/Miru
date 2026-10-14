"""The durable half of the SSE stream: a persistence sink for `Event`s that a reconnecting
client replays from after a dropped connection.

The live fan-out (`bus.py`) is deliberately separate from this — the bus is a pure, swappable
pub/sub; this store is where a subscriber writes each enveloped event so `Last-Event-ID` resume
has something to read. Writes respect the `UNIQUE(turn_id, event_seq)` guard: a redelivered or
double-published event collides and is treated as already-recorded, never a crash — the same
at-least-once-write / exactly-once-effect discipline the rest of the codebase uses. A write with
no user or thread scope is skipped rather than raised, so a transport-level publish never fails
on a correlation id it cannot attribute. The turn id need not be a persisted turn: a
regenerated answer streams under an id that is saved only once the answer is complete.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Protocol, runtime_checkable

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import StreamEventRow
from events.bus import Event

logger = structlog.get_logger(__name__)


@runtime_checkable
class StreamEventStore(Protocol):
    """The write/read seam for the durable replay log.

    `append` is idempotent on `(turn_id, event_seq)`; `replay_since` returns every event *strictly
    after* `after_seq` for the turn, in `event_seq` order — the exact set a client that last saw
    `after_seq` is missing, with no overlap of what it already has.
    """

    async def append(
        self, event: Event, *, user_id: str | None = None, thread_id: str | None = None
    ) -> bool: ...

    async def replay_since(self, turn_id: str, after_seq: int) -> list[Event]: ...


class SqlAlchemyStreamEventStore:
    """`StreamEventStore` backed by the `stream_events` table. Each call opens its own short
    session from the injected factory — the store is safe to share across the many concurrent
    subscribers a multi-worker deployment fans an event out to, since no session is held between
    calls."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append(
        self, event: Event, *, user_id: str | None = None, thread_id: str | None = None
    ) -> bool:
        """Persist one event. Returns `True` if a row was written, `False` if it was a no-op
        (duplicate `(turn_id, event_seq)`, or no user or thread scope to attribute the row to).
        Never raises on those expected cases — a durable-log write is best-effort relative to the
        live path, never a reason to fail the turn."""
        if event.turn_id is None or user_id is None or thread_id is None:
            return False
        row = StreamEventRow(
            id=str(uuid.uuid4()),
            turn_id=event.turn_id,
            thread_id=thread_id,
            user_id=user_id,
            event_seq=event.seq,
            event_type=event.kind,
            agent_id=event.agent_id,
            parent_agent_id=event.parent_agent_id,
            node_id=event.node_id,
            data=dict(event.data),
            created_at=event.ts if event.ts.tzinfo else event.ts.replace(tzinfo=UTC),
        )
        try:
            async with self._session_factory() as session:
                session.add(row)
                await session.commit()
        except IntegrityError:
            # The UNIQUE(turn_id, event_seq) guard: a redelivered/duplicate publish. Expected,
            # benign, and means "nothing new to record" — the replay log stays consistent.
            logger.debug("stream_event_append_noop", turn_id=event.turn_id, seq=event.seq)
            return False
        return True

    async def replay_since(self, turn_id: str, after_seq: int) -> list[Event]:
        """Every persisted event for `turn_id` with `event_seq` strictly greater than `after_seq`,
        oldest first. `after_seq` is the client's `Last-Event-ID`; strict `>` is what guarantees
        the client never re-receives the event it already has (no duplicate) while still getting
        everything it missed (no gap)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(StreamEventRow)
                .where(StreamEventRow.turn_id == turn_id, StreamEventRow.event_seq > after_seq)
                .order_by(StreamEventRow.event_seq)
            )
            rows = result.scalars().all()
        return [
            Event(
                kind=row.event_type,
                seq=row.event_seq,
                ts=row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC),
                turn_id=row.turn_id,
                agent_id=row.agent_id,
                parent_agent_id=row.parent_agent_id,
                node_id=row.node_id,
                data=dict(row.data),
            )
            for row in rows
        ]


__all__ = ["SqlAlchemyStreamEventStore", "StreamEventStore"]
