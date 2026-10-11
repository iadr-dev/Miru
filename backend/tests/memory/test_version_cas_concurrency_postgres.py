"""Optimistic `version` checks under genuinely concurrent edits, against a REAL Postgres.

The hazard: the check is "read the row, compare its version in Python, then write", so two devices
editing the same group at once both read version N, both pass, and the last write silently wins
(a lost update). With the row lock taken when an edit reads the row, the second editor waits, then
sees version N+1 and is rejected - so of N simultaneous edits made from the same version, exactly
one succeeds and the rest get the stale-version error (HTTP 409).

Skips (never fails) when `TEST_POSTGRES_URL` is unset; the CI concurrency leg sets it.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from memory.watchlists import StaleWatchlistVersionError, WatchlistStore
from schemas.enums import Market
from tests.pg_support import insert_user

pytestmark = pytest.mark.concurrency

_EDITORS = 8


async def _group_for(factory: async_sessionmaker[object], user_id: str) -> str:
    async with factory() as session:  # type: ignore[union-attr]
        group = await WatchlistStore(session).create_group(
            user_id=user_id, name="start", market=Market.TW, asset_class="stock"
        )
        await session.commit()
    return group.id


async def test_concurrent_edits_from_the_same_version_let_exactly_one_win(
    pg_session_factory: async_sessionmaker[object],
) -> None:
    user_id = await insert_user(pg_session_factory)
    group_id = await _group_for(pg_session_factory, user_id)
    # Every editor connects first, then all start together: a fresh connection takes tens of
    # milliseconds, so without the barrier the first editor would finish before the others had
    # even connected and nothing would overlap.
    start = asyncio.Barrier(_EDITORS)

    async def rename(index: int) -> bool:
        async with pg_session_factory() as session:  # type: ignore[union-attr]
            await session.execute(text("select 1"))
            await start.wait()
            try:
                await WatchlistStore(session).rename_group(
                    user_id=user_id,
                    watchlist_id=group_id,
                    name=f"editor-{index}",
                    expected_version=0,
                )
            except StaleWatchlistVersionError:
                await session.rollback()
                return False
            await session.commit()
            return True

    outcomes = await asyncio.gather(*(rename(i) for i in range(_EDITORS)))

    assert sum(outcomes) == 1, f"{sum(outcomes)} editors saved from version 0; exactly one may win"
    async with pg_session_factory() as session:  # type: ignore[union-attr]
        groups = await WatchlistStore(session).list_groups(user_id)
    assert [g.version for g in groups if g.id == group_id] == [1]
