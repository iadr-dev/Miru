"""Erasing an account removes the user's rows from every table, and only that user's."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accounts.erasure import erase_user, user_scoped_tables
from db.base import Base
from db.models import ThreadRow, UserRow, WatchlistRow
from schemas.enums import Tier
from tests.scheduler.conftest import engine, insert_user, session_factory  # noqa: F401 - fixtures


async def _seed(session: AsyncSession, user_id: str) -> None:
    now = datetime.now(UTC)
    session.add(
        ThreadRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title="t",
            created_at=now,
            updated_at=now,
            last_message_at=now,
        )
    )
    session.add(
        WatchlistRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name="w",
            market="TW",
            asset_class="stock",
            sort_order="a0",
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()


async def _count(session: AsyncSession, model: type, user_id: str) -> int:
    column = model.user_id  # type: ignore[attr-defined]
    return int(
        (
            await session.execute(select(func.count()).select_from(model).where(column == user_id))
        ).scalar_one()
    )


async def test_erasing_removes_the_users_rows_everywhere_and_leaves_other_users(
    session_factory: async_sessionmaker[AsyncSession],  # noqa: F811 - the imported fixture
) -> None:
    doomed = await insert_user(session_factory, tier=Tier.PRO)
    kept = await insert_user(session_factory, tier=Tier.FREE)
    async with session_factory() as session:
        await _seed(session, doomed)
        await _seed(session, kept)
        await session.commit()

        removed = await erase_user(session, doomed)
        await session.commit()

        assert removed["threads"] == 1
        assert removed["watchlists"] == 1
        assert removed["users"] == 1
        assert await _count(session, ThreadRow, doomed) == 0
        assert await _count(session, WatchlistRow, doomed) == 0
        assert await session.get(UserRow, doomed) is None
        assert await _count(session, ThreadRow, kept) == 1
        assert await session.get(UserRow, kept) is not None


def test_every_table_keyed_by_a_user_is_covered() -> None:
    """A table whose per-user key is not called `user_id` would silently survive an erasure."""
    covered = set(user_scoped_tables()) | {"users"}
    uncovered = [
        table.name
        for table in Base.metadata.sorted_tables
        if table.name not in covered and any("user" in column.name for column in table.c)
    ]
    assert uncovered == [], f"tables with a user-ish column not purged on erasure: {uncovered}"
