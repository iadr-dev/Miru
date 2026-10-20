"""Two scheduler workers must not run the same job, and only one scheduler process holds the lease.

`due_jobs` is a scan, so two workers (a restart overlap, a lease handover) can both see a job. The
claim is one atomic UPDATE: exactly one worker wins it. Postgres only."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest

from ingest.leader import build_leader_election
from scheduler.models import (
    DeliveryChannelKind,
    JobStatus,
    ScheduleAction,
    ScheduleTrigger,
    TriggerType,
)
from scheduler.store import RUN_TIMEOUT, SqlAlchemySchedulerStore
from tests.pg_support import insert_user

pytestmark = pytest.mark.concurrency

_WORKERS = 8


async def _due_job(factory, user_id: str):
    async with factory() as session:
        store = SqlAlchemySchedulerStore(session)
        job = await store.create_job(
            user_id=user_id,
            trigger=ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec="after_close"),
            action=ScheduleAction(skill_ref="daily-context-brief", inputs={}),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
            next_run=datetime.now(UTC) - timedelta(minutes=1),
        )
        await session.commit()
        return job


async def _claim(factory, job_id: str, now: datetime) -> bool:
    async with factory() as session:
        won = await SqlAlchemySchedulerStore(session).claim_job(job_id=job_id, now=now)
        await session.commit()
        return won


async def test_exactly_one_of_many_workers_claims_a_due_job(pg_session_factory) -> None:
    user = await insert_user(pg_session_factory)
    job = await _due_job(pg_session_factory, user)
    now = datetime.now(UTC)

    results = await asyncio.gather(
        *(_claim(pg_session_factory, job.id, now) for _ in range(_WORKERS))
    )

    assert results.count(True) == 1


async def test_a_running_job_is_not_claimed_again_until_its_run_is_orphaned(
    pg_session_factory,
) -> None:
    user = await insert_user(pg_session_factory)
    job = await _due_job(pg_session_factory, user)
    now = datetime.now(UTC)
    assert await _claim(pg_session_factory, job.id, now)

    assert not await _claim(pg_session_factory, job.id, now)  # being run
    assert not await _claim(pg_session_factory, job.id, now + RUN_TIMEOUT - timedelta(seconds=30))
    assert await _claim(pg_session_factory, job.id, now + RUN_TIMEOUT + timedelta(minutes=1))


async def test_a_job_that_finished_and_moved_on_is_not_claimed_by_a_late_worker(
    pg_session_factory,
) -> None:
    user = await insert_user(pg_session_factory)
    job = await _due_job(pg_session_factory, user)
    now = datetime.now(UTC)
    assert await _claim(pg_session_factory, job.id, now)
    async with pg_session_factory() as session:  # the winner finished: next occurrence is tomorrow
        store = SqlAlchemySchedulerStore(session)
        await store.set_job_status(
            job_id=job.id, status=JobStatus.OK, last_run=now, next_run=now + timedelta(days=1)
        )
        await session.commit()

    assert not await _claim(pg_session_factory, job.id, now)  # the late worker saw it due


async def test_only_one_scheduler_process_holds_the_lease() -> None:
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set - Postgres concurrency check skipped")
    first = build_leader_election(role="scheduler", database_url=url, scope_root=".")
    second = build_leader_election(role="scheduler", database_url=url, scope_root=".")
    try:
        assert await first.acquire() is True
        assert await second.acquire() is False  # a second scheduler stands by
        assert first.is_leader()
        assert not second.is_leader()
        await first.release()
        assert await second.refresh() is True  # and takes over once the first is gone
    finally:
        await first.release()
        await second.release()
