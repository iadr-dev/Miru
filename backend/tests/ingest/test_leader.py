"""Leader election: exactly one holder of the writer lease at a time, and takeover when it goes."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ingest.leader import (
    AlwaysLeader,
    FileLockLeader,
    NeverLeader,
    PostgresAdvisoryLeader,
    acquire_leadership,
    build_leader_election,
    lease_key,
    refresh_leadership,
    release_leadership,
)


def test_the_lease_key_is_stable_distinct_per_scope_and_a_signed_64_bit_int() -> None:
    a = lease_key("ingest-writer", "/data/a")
    assert a == lease_key("ingest-writer", "/data/a")
    assert a != lease_key("ingest-writer", "/data/b")
    assert a != lease_key("scheduler", "/data/a")
    assert -(2**63) <= a < 2**63


async def test_a_plain_leader_needs_no_lifecycle() -> None:
    assert await acquire_leadership(AlwaysLeader())
    assert not await acquire_leadership(NeverLeader())
    assert await refresh_leadership(AlwaysLeader())
    await release_leadership(AlwaysLeader())  # nothing to release; must not raise


async def test_only_one_file_lock_leader_holds_the_lease(tmp_path: Path) -> None:
    first = FileLockLeader(tmp_path / "w.lease", name="ingest-writer")
    second = FileLockLeader(tmp_path / "w.lease", name="ingest-writer")
    assert await first.acquire()
    assert first.is_leader()
    assert not await second.acquire()
    assert not second.is_leader()

    await first.release()
    assert not first.is_leader()
    assert await second.refresh()  # a standby takes over on its next pass
    assert second.is_leader()
    await second.release()


def test_the_factory_picks_the_lease_from_the_database_url(tmp_path: Path) -> None:
    sqlite = build_leader_election(
        role="ingest-writer", database_url="sqlite+aiosqlite:///x.db", scope_root=tmp_path
    )
    postgres = build_leader_election(
        role="ingest-writer",
        database_url="postgresql+asyncpg://u:p@localhost/db",
        scope_root=tmp_path,
    )
    assert isinstance(sqlite, FileLockLeader)
    assert isinstance(postgres, PostgresAdvisoryLeader)
    assert not sqlite.is_leader()  # nothing is held until the process acquires it
    assert not postgres.is_leader()


@pytest.mark.concurrency
async def test_postgres_lease_is_exclusive_and_freed_when_the_holder_dies(tmp_path: Path) -> None:
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set - Postgres concurrency check skipped")
    key = lease_key("ingest-writer", str(tmp_path))
    first = PostgresAdvisoryLeader(url, key=key, name="ingest-writer")
    second = PostgresAdvisoryLeader(url, key=key, name="ingest-writer")
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        assert await first.acquire()
        assert not await second.acquire()

        # Kill the leader's database session, as a crash or a network drop would. Postgres frees
        # the advisory lock by itself; no timeout, no cleanup job.
        async with admin.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_locks "
                    "WHERE locktype = 'advisory' AND granted AND pid <> pg_backend_pid()"
                )
            )
        # pg_terminate_backend is asynchronous, and a real standby retries every poll interval, so
        # the standby gets a few attempts too.
        for _ in range(50):
            if await second.refresh():
                break
            await asyncio.sleep(0.1)
        assert second.is_leader()  # the standby takes over
        assert not await first.refresh()  # the old leader notices it lost the lease...
        assert not first.is_leader()  # ...and stops reporting leadership
    finally:
        await first.release()
        await second.release()
        await admin.dispose()
