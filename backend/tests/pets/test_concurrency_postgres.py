"""The pet counters under genuinely concurrent requests, against a REAL Postgres.

Hazards (SQLite has one writer, so the races are latent there; hence Postgres-only):
- two simultaneous pulls with one pull available both pass the "any left?" check and both succeed
  (a double spend), and can both insert the same pet;
- simultaneous chat rewards read the same `xp_today` and together exceed the daily XP cap.

The fix serializes mutations per user with a row lock on `user_pet_stats`. Skips (never fails) when
`TEST_POSTGRES_URL` is unset; the CI concurrency leg sets it against a service container.
"""

from __future__ import annotations

import asyncio
import random

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import UserPetRow, UserPetStatsRow
from pets import config
from pets.store import NoPullsAvailableError, SqlAlchemyPetStore
from tests.pg_support import insert_user

pytestmark = pytest.mark.concurrency

_CONCURRENT = 12


async def _pull(factory: async_sessionmaker[object], user_id: str) -> bool:
    """One request: its own session and transaction, like a real API call. True if it pulled."""
    async with factory() as session:  # type: ignore[union-attr]
        store = SqlAlchemyPetStore(session, rng=random.Random())
        try:
            await store.pull(user_id=user_id)
        except NoPullsAvailableError:
            await session.rollback()
            return False
        await session.commit()
        return True


async def _chat_reward(factory: async_sessionmaker[object], user_id: str) -> int:
    async with factory() as session:  # type: ignore[union-attr]
        reward = await SqlAlchemyPetStore(session).grant_chat_xp(user_id=user_id)
        await session.commit()
        return reward.xp


async def test_concurrent_pulls_spend_exactly_the_available_pulls(
    pg_session_factory: async_sessionmaker[object],
) -> None:
    user_id = await insert_user(pg_session_factory)
    async with pg_session_factory() as session:  # type: ignore[union-attr]
        await SqlAlchemyPetStore(session).ensure_stats(user_id=user_id)  # seeds 1 free pull
        await session.commit()

    outcomes = await asyncio.gather(
        *(_pull(pg_session_factory, user_id) for _ in range(_CONCURRENT))
    )

    assert sum(outcomes) == 1, "one available pull must be spendable exactly once"
    async with pg_session_factory() as session:  # type: ignore[union-attr]
        stats = (
            await session.execute(select(UserPetStatsRow).where(UserPetStatsRow.user_id == user_id))
        ).scalar_one()
        owned = (
            await session.execute(
                select(func.count()).select_from(UserPetRow).where(UserPetRow.user_id == user_id)
            )
        ).scalar_one()
    assert stats.available_pulls == 0
    assert stats.total_pulls == 1
    assert owned == 1


async def test_concurrent_chat_rewards_never_exceed_the_daily_xp_cap(
    pg_session_factory: async_sessionmaker[object],
) -> None:
    user_id = await insert_user(pg_session_factory)

    granted = await asyncio.gather(
        *(_chat_reward(pg_session_factory, user_id) for _ in range(_CONCURRENT * 4))
    )

    total = sum(granted)
    assert total <= config.DAILY_CHAT_XP_CAP, (
        f"{total} XP granted, cap is {config.DAILY_CHAT_XP_CAP}"
    )
    async with pg_session_factory() as session:  # type: ignore[union-attr]
        xp_today = (
            await session.execute(
                text("select xp_today from user_pet_stats where user_id = :u"), {"u": user_id}
            )
        ).scalar_one()
    assert xp_today == total


async def test_first_requests_create_one_stats_row(
    pg_session_factory: async_sessionmaker[object],
) -> None:
    user_id = await insert_user(pg_session_factory)

    async def ensure() -> None:
        async with pg_session_factory() as session:  # type: ignore[union-attr]
            await SqlAlchemyPetStore(session).ensure_stats(user_id=user_id)
            await session.commit()

    await asyncio.gather(*(ensure() for _ in range(_CONCURRENT)))

    async with pg_session_factory() as session:  # type: ignore[union-attr]
        count = (
            await session.execute(
                select(func.count())
                .select_from(UserPetStatsRow)
                .where(UserPetStatsRow.user_id == user_id)
            )
        ).scalar_one()
    assert count == 1
