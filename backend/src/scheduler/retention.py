"""Deleting data that has outlived its purpose.

`stream_events` is the replay log a client reads after a dropped connection. A reconnect happens
within seconds or minutes of the drop, so rows older than a few days are never read again, yet every
streamed turn writes dozens of them: left alone the table grows with every chat, for ever.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import StreamEventRow

logger = structlog.get_logger(__name__)

STREAM_EVENT_RETENTION = timedelta(days=7)
# Deleted in slices so one pass never holds a long lock on a table the live API is appending to.
_BATCH_SIZE = 5000


async def prune_stream_events(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    keep: timedelta = STREAM_EVENT_RETENTION,
) -> int:
    """Delete replay-log rows older than `keep`. Returns how many were removed. Safe to run from
    several processes at once: a row another pass already took is simply not there to delete."""
    cutoff = now - keep
    removed = 0
    while True:
        async with session_factory() as session:
            stale = select(StreamEventRow.id).where(StreamEventRow.created_at < cutoff)
            result = await session.execute(
                delete(StreamEventRow).where(StreamEventRow.id.in_(stale.limit(_BATCH_SIZE)))
            )
            await session.commit()
        batch = int(getattr(result, "rowcount", 0) or 0)
        removed += batch
        if batch < _BATCH_SIZE:
            break
    if removed:
        logger.info("stream_events_pruned", removed=removed, older_than=cutoff.isoformat())
    return removed


__all__ = ["STREAM_EVENT_RETENTION", "prune_stream_events"]
