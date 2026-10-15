"""The SSE replay log is pruned: old rows go, recent rows stay, in slices, at most hourly."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import StreamEventRow, ThreadRow
from ingest.leader import AlwaysLeader
from scheduler import retention
from scheduler.retention import STREAM_EVENT_RETENTION, prune_stream_events
from scheduler.worker import SchedulerWorker
from tests.scheduler.test_worker import _ctx, _StubOrchestrator, _StubRouter

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


async def _seed(factory: async_sessionmaker[AsyncSession], ages: list[timedelta]) -> None:
    thread_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(
            ThreadRow(
                id=thread_id, user_id="u1", last_message_at=NOW, created_at=NOW, updated_at=NOW
            )
        )
        await session.flush()
        for seq, age in enumerate(ages):
            session.add(
                StreamEventRow(
                    id=str(uuid.uuid4()),
                    turn_id="t1",
                    thread_id=thread_id,
                    user_id="u1",
                    event_seq=seq,
                    event_type="message",
                    data={},
                    created_at=NOW - age,
                )
            )
        await session.commit()


async def _count(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        return int(
            (await session.execute(select(func.count()).select_from(StreamEventRow))).scalar_one()
        )


async def test_old_rows_are_removed_and_recent_rows_kept(session_factory) -> None:
    await _seed(
        session_factory,
        [timedelta(days=30), timedelta(days=8), timedelta(days=6), timedelta(minutes=1)],
    )

    removed = await prune_stream_events(session_factory, now=NOW)

    assert removed == 2
    assert await _count(session_factory) == 2


async def test_pruning_works_through_more_than_one_slice(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retention, "_BATCH_SIZE", 3)
    await _seed(session_factory, [timedelta(days=9)] * 7 + [timedelta(hours=1)])

    removed = await prune_stream_events(session_factory, now=NOW)

    assert removed == 7
    assert await _count(session_factory) == 1


async def test_nothing_to_prune_is_a_no_op(session_factory) -> None:
    await _seed(session_factory, [timedelta(hours=1)])

    assert await prune_stream_events(session_factory, now=NOW) == 0
    assert timedelta(days=1) <= STREAM_EVENT_RETENTION  # a reconnect is never cut off mid-day


async def test_the_worker_prunes_at_most_once_an_hour(session_factory) -> None:
    worker = SchedulerWorker(
        _ctx(
            session_factory,
            orchestrator=_StubOrchestrator(None),  # type: ignore[arg-type]
            router=_StubRouter({}),
            leader=AlwaysLeader(),
            quota_gate=object(),
        )
    )
    await _seed(session_factory, [timedelta(days=9)])
    assert await worker.prune_expired(now=NOW) == 1

    await _seed(session_factory, [timedelta(days=9)])
    assert await worker.prune_expired(now=NOW + timedelta(minutes=30)) == 0  # too soon
    assert await worker.prune_expired(now=NOW + timedelta(hours=1, minutes=1)) == 1
