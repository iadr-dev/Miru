"""Concurrency-matrix proof for the `usage_counters` atomic increment, run against a REAL Postgres
with genuinely concurrent connections — not SQLite (single-writer, so the race is latent there) and
not a serialized in-process loop.

The hazard: two parallel turns for the same user in the same window both pass the daily-chat check
and over-admit past the cap (a lost update on read-modify-write). The mechanism is a single atomic
`INSERT … ON CONFLICT DO UPDATE SET n = n + 1 RETURNING n` — the check and the increment are one
statement, so N concurrent claimants against a finite cap admit exactly `cap`, never more.

Skips (never fails) when `TEST_POSTGRES_URL` is unset, so the default local suite is unaffected; the
CI concurrency leg sets it against a service container. Postgres-only on purpose: proving the race
is closed requires a store that actually allows concurrent writers.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.base import make_engine, make_session_factory
from db.models import UserRow
from usage.store import SqlAlchemyUsageStore

pytestmark = pytest.mark.concurrency

_CAP = 5
_CONCURRENT_CLAIMS = 40


@pytest.fixture
async def pg_session_factory() -> AsyncIterator[async_sessionmaker[object]]:
    pg_url = os.environ.get("TEST_POSTGRES_URL")
    if not pg_url:
        pytest.skip("TEST_POSTGRES_URL not set — Postgres concurrency check skipped")
    engine = make_engine(pg_url)
    # The migration chain owns the real schema; the CI job runs migrations first. For an isolated
    # test run, create just the tables the ORM knows about, then drop them after.
    from db.base import Base  # noqa: PLC0415 — local import keeps module load light

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield make_session_factory(engine)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def _insert_user(session_factory: async_sessionmaker[object]) -> str:
    now = datetime.now(UTC)
    user_id = str(uuid.uuid4())
    async with session_factory() as session:  # type: ignore[union-attr]
        session.add(
            UserRow(
                id=user_id,
                email=f"{user_id}@example.test",
                display_name="Race",
                tier="free",
                personality_id="steady",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return user_id


async def test_daily_chat_reservation_never_over_admits(
    pg_session_factory: async_sessionmaker[object],
) -> None:
    store = SqlAlchemyUsageStore(pg_session_factory)  # type: ignore[arg-type]
    user_id = await _insert_user(pg_session_factory)

    # Fire many claims at once against a finite cap. Each opens its own connection from the pool, so
    # these are real concurrent writers contending for the same counter row.
    verdicts = await asyncio.gather(
        *(store.reserve_daily_chat(user_id=user_id, limit=_CAP) for _ in range(_CONCURRENT_CLAIMS))
    )

    admitted = [v for v in verdicts if v.allowed]
    assert len(admitted) == _CAP, f"expected exactly {_CAP} admitted, got {len(admitted)}"

    # The durable counter must have settled at exactly the cap — no lost update inflated or lost a
    # claim, and the refund-on-refuse path left no phantom increments behind.
    async with pg_session_factory() as session:  # type: ignore[union-attr]
        final = await session.execute(
            text("SELECT n FROM usage_counters WHERE user_id = :uid AND counter = 'chat'"),
            {"uid": user_id},
        )
        assert int(final.scalar_one()) == _CAP
