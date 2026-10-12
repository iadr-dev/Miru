"""Account erasure (GDPR/PDPA "delete my account and all data").

Most per-user tables carry a `user_id` that is a bare indexed column rather than a foreign key to
`users` (the isolation-scoping design in `db/models/`), so deleting the `users` row would leave
those rows behind. This purges every table that has a `user_id` column - discovered from the ORM
metadata, so a table added later is covered without remembering to list it - and then the user.
Cancelling an external billing subscription is the caller's job and must happen first."""

from __future__ import annotations

from typing import cast

import structlog
from sqlalchemy import CursorResult, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.base import Base
from db.models import UserRow

logger = structlog.get_logger(__name__)

_USER_COLUMN = "user_id"


def user_scoped_tables() -> list[str]:
    """Names of every table that stores rows per user, children before parents."""
    return [
        table.name
        for table in reversed(Base.metadata.sorted_tables)
        if _USER_COLUMN in table.c and table.name != UserRow.__tablename__
    ]


async def erase_user(session: AsyncSession, user_id: str) -> dict[str, int]:
    """Delete everything stored for `user_id`, then the user. Returns rows removed per table
    (tables with none are omitted). The caller commits."""
    removed: dict[str, int] = {}
    tables = {table.name: table for table in Base.metadata.sorted_tables}
    for name in user_scoped_tables():
        result = await session.execute(
            delete(tables[name]).where(tables[name].c.user_id == user_id)
        )
        count = int(cast("CursorResult[tuple[()]]", result).rowcount or 0)
        if count:
            removed[name] = count
    users = await session.execute(delete(UserRow).where(UserRow.id == user_id))
    deleted_users = int(cast("CursorResult[tuple[()]]", users).rowcount or 0)
    if deleted_users:
        removed[UserRow.__tablename__] = deleted_users
    logger.info("account_erased", user_id=user_id, tables=len(removed))
    return removed
