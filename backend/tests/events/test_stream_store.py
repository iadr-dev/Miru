"""Durable `stream_events` store: idempotent append (UNIQUE(turn_id, event_seq) guard), the
no-scope no-op path, and the exact `replay_since` boundary (strict `> after_seq`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import StreamEventRow, ThreadRow
from events.bus import Event
from events.stream_store import SqlAlchemyStreamEventStore


async def _seed_thread(factory: async_sessionmaker, *, user_id: str = "u1") -> str:
    """Insert a real thread (the rows' owner, so they go with it); return its id."""
    now = datetime.now(UTC)
    thread_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(
            ThreadRow(
                id=thread_id,
                user_id=user_id,
                last_message_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return thread_id


def _event(turn_id: str, kind: str, seq: int) -> Event:
    return Event(kind=kind, seq=seq, ts=datetime.now(UTC), turn_id=turn_id, data={"n": seq})


async def test_append_then_replay_round_trips(session_factory: async_sessionmaker) -> None:
    thread_id = await _seed_thread(session_factory)
    turn_id = str(uuid.uuid4())
    store = SqlAlchemyStreamEventStore(session_factory)

    for seq, kind in enumerate(["turn.start", "message", "turn.end"]):
        assert (
            await store.append(_event(turn_id, kind, seq), user_id="u1", thread_id=thread_id)
            is True
        )

    replayed = await store.replay_since(turn_id, after_seq=-1)
    assert [e.kind for e in replayed] == ["turn.start", "message", "turn.end"]
    assert [e.seq for e in replayed] == [0, 1, 2]


async def test_duplicate_seq_is_noop_not_crash(session_factory: async_sessionmaker) -> None:
    thread_id = await _seed_thread(session_factory)
    turn_id = str(uuid.uuid4())
    store = SqlAlchemyStreamEventStore(session_factory)

    assert (
        await store.append(_event(turn_id, "message", 0), user_id="u1", thread_id=thread_id) is True
    )
    # Same (turn_id, seq) again — the UNIQUE guard makes this a no-op, not an error.
    assert (
        await store.append(_event(turn_id, "message", 0), user_id="u1", thread_id=thread_id)
        is False
    )

    replayed = await store.replay_since(turn_id, after_seq=-1)
    assert len(replayed) == 1  # not duplicated


async def test_a_turn_id_that_is_not_saved_yet_is_still_recorded(
    session_factory: async_sessionmaker,
) -> None:
    """A regenerated answer streams under an id whose turn row exists only once the answer is
    saved. Its events must still be replayable, or a reconnect mid-answer loses the middle."""
    thread_id = await _seed_thread(session_factory)
    store = SqlAlchemyStreamEventStore(session_factory)

    assert await store.append(
        _event("not-saved-yet", "message", 0), user_id="u1", thread_id=thread_id
    )

    assert [e.seq for e in await store.replay_since("not-saved-yet", after_seq=-1)] == [0]


async def test_events_go_with_their_thread(session_factory: async_sessionmaker) -> None:
    thread_id = await _seed_thread(session_factory)
    store = SqlAlchemyStreamEventStore(session_factory)
    await store.append(_event("t-1", "message", 0), user_id="u1", thread_id=thread_id)

    async with session_factory() as session:
        await session.execute(delete(ThreadRow).where(ThreadRow.id == thread_id))
        await session.commit()
        remaining = (await session.execute(select(StreamEventRow))).scalars().all()

    assert remaining == []


async def test_no_user_or_thread_scope_is_noop(session_factory: async_sessionmaker) -> None:
    thread_id = await _seed_thread(session_factory)
    store = SqlAlchemyStreamEventStore(session_factory)
    assert (
        await store.append(_event("t-1", "message", 0), user_id=None, thread_id=thread_id) is False
    )
    assert await store.append(_event("t-1", "message", 0), user_id="u1", thread_id=None) is False


async def test_replay_boundary_is_strictly_greater(session_factory: async_sessionmaker) -> None:
    """`replay_since(turn_id, N)` returns exactly seq > N — the client that last saw N never
    re-receives N (no duplicate) and gets everything after it (no gap)."""
    thread_id = await _seed_thread(session_factory)
    turn_id = str(uuid.uuid4())
    store = SqlAlchemyStreamEventStore(session_factory)
    for seq in range(5):  # seq 0..4
        await store.append(_event(turn_id, "message", seq), user_id="u1", thread_id=thread_id)

    after_2 = await store.replay_since(turn_id, after_seq=2)
    assert [e.seq for e in after_2] == [3, 4]  # 2 itself is NOT re-sent; 3,4 are

    from_start = await store.replay_since(turn_id, after_seq=-1)
    assert [e.seq for e in from_start] == [0, 1, 2, 3, 4]

    past_end = await store.replay_since(turn_id, after_seq=4)
    assert past_end == []  # nothing after the last seq — no gap invented
