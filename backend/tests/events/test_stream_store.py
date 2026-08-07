"""Durable `stream_events` store: idempotent append (UNIQUE(turn_id, event_seq) guard), the
FK/no-scope no-op path, and the exact `replay_since` boundary (strict `> after_seq`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import ThreadRow, TurnRow
from events.bus import Event
from events.stream_store import SqlAlchemyStreamEventStore


async def _seed_turn(factory: async_sessionmaker, *, user_id: str = "u1") -> str:
    """Insert a real thread + turn so the stream_events FK is satisfiable; return the turn id."""
    now = datetime.now(UTC)
    thread_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
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
        await session.flush()
        session.add(
            TurnRow(
                id=turn_id,
                thread_id=thread_id,
                user_id=user_id,
                role="assistant",
                status="running",
                created_at=now,
            )
        )
        await session.commit()
    return turn_id


def _event(turn_id: str, kind: str, seq: int) -> Event:
    return Event(kind=kind, seq=seq, ts=datetime.now(UTC), turn_id=turn_id, data={"n": seq})


async def test_append_then_replay_round_trips(session_factory: async_sessionmaker) -> None:
    turn_id = await _seed_turn(session_factory)
    store = SqlAlchemyStreamEventStore(session_factory)

    for seq, kind in enumerate(["turn.start", "message", "turn.end"]):
        assert await store.append(_event(turn_id, kind, seq), user_id="u1") is True

    replayed = await store.replay_since(turn_id, after_seq=-1)
    assert [e.kind for e in replayed] == ["turn.start", "message", "turn.end"]
    assert [e.seq for e in replayed] == [0, 1, 2]


async def test_duplicate_seq_is_noop_not_crash(session_factory: async_sessionmaker) -> None:
    turn_id = await _seed_turn(session_factory)
    store = SqlAlchemyStreamEventStore(session_factory)

    assert await store.append(_event(turn_id, "message", 0), user_id="u1") is True
    # Same (turn_id, seq) again — the UNIQUE guard makes this a no-op, not an error.
    assert await store.append(_event(turn_id, "message", 0), user_id="u1") is False

    replayed = await store.replay_since(turn_id, after_seq=-1)
    assert len(replayed) == 1  # not duplicated


async def test_unknown_turn_id_is_noop(session_factory: async_sessionmaker) -> None:
    """An ephemeral/never-persisted turn id violates the turns FK — a benign no-op (the live path
    still delivers), never a crash that fails the turn."""
    store = SqlAlchemyStreamEventStore(session_factory)
    assert await store.append(_event("not-a-real-turn", "message", 0), user_id="u1") is False


async def test_no_user_scope_is_noop(session_factory: async_sessionmaker) -> None:
    turn_id = await _seed_turn(session_factory)
    store = SqlAlchemyStreamEventStore(session_factory)
    assert await store.append(_event(turn_id, "message", 0), user_id=None) is False


async def test_replay_boundary_is_strictly_greater(session_factory: async_sessionmaker) -> None:
    """`replay_since(turn_id, N)` returns exactly seq > N — the client that last saw N never
    re-receives N (no duplicate) and gets everything after it (no gap)."""
    turn_id = await _seed_turn(session_factory)
    store = SqlAlchemyStreamEventStore(session_factory)
    for seq in range(5):  # seq 0..4
        await store.append(_event(turn_id, "message", seq), user_id="u1")

    after_2 = await store.replay_since(turn_id, after_seq=2)
    assert [e.seq for e in after_2] == [3, 4]  # 2 itself is NOT re-sent; 3,4 are

    from_start = await store.replay_since(turn_id, after_seq=-1)
    assert [e.seq for e in from_start] == [0, 1, 2, 3, 4]

    past_end = await store.replay_since(turn_id, after_seq=4)
    assert past_end == []  # nothing after the last seq — no gap invented
