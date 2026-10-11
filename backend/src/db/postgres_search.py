"""Postgres-only index that keeps chat search from scanning every message a user has.

`ConversationStore.search_threads` matches `lower(CAST(content AS VARCHAR)) LIKE '%q%'` over the
caller's text messages. A leading wildcard cannot use a B-tree, so Postgres read and cast every
message row to text on each search. A trigram GIN index (`pg_trgm`) serves exactly that predicate.

It is declared here, not on the model, because it needs an extension and an operator class that
only exist on Postgres (SQLite has no `gin`). The expression and the `type = 'text'` predicate must
match the query's text exactly for the planner to use the index. Creating the extension needs
privileges some managed databases withhold, so a failure is logged and search simply stays
correct but slower."""

from __future__ import annotations

import structlog
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger(__name__)

MESSAGE_TEXT_INDEX = "ix_messages_text_trgm"

_STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    f"CREATE INDEX IF NOT EXISTS {MESSAGE_TEXT_INDEX} ON messages "
    "USING gin (lower(CAST(content AS VARCHAR)) gin_trgm_ops) WHERE type = 'text'",
)


async def ensure_postgres_search_index(engine: AsyncEngine) -> None:
    """Create the trigram index if missing. A no-op on other dialects; idempotent, and safe when
    several replicas start at once (a lost race is just another no-op)."""
    if engine.dialect.name != "postgresql":
        return
    try:
        async with engine.begin() as conn:
            for statement in _STATEMENTS:
                await conn.exec_driver_sql(statement)
    except DBAPIError:
        logger.warning("search_index_unavailable", index=MESSAGE_TEXT_INDEX, exc_info=True)


__all__ = ["MESSAGE_TEXT_INDEX", "ensure_postgres_search_index"]
