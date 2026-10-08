"""The user's own switch for long-term memory.

Off means: nothing is recalled into a turn and the agent saves nothing new. Existing memories are
kept (the user deletes them explicitly), so turning it back on loses nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import UserRow


async def memory_enabled(session: AsyncSession, *, user_id: str) -> bool:
    result = await session.execute(select(UserRow.memory_enabled).where(UserRow.id == user_id))
    value = result.scalar_one_or_none()
    return True if value is None else bool(value)


async def set_memory_enabled(session: AsyncSession, *, user_id: str, enabled: bool) -> None:
    result = await session.execute(select(UserRow).where(UserRow.id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        return
    row.memory_enabled = enabled
    row.updated_at = datetime.now(UTC)
    await session.flush()


__all__ = ["memory_enabled", "set_memory_enabled"]
