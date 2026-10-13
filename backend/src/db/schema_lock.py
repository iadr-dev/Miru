"""One process at a time creates the schema.

The API, scheduler, ingest daemon and MCP server each call `ensure_schema` at startup, and compose
starts them together. `create_all` checks whether a table exists and then creates it, which is not
atomic, so on an empty Postgres two of them can both decide a table is missing and one fails with
"relation already exists". A Postgres advisory lock held on a dedicated connection for the whole
provisioning serialises them; the later ones then find everything in place and do nothing.
SQLite is single-writer anyway, so this is a no-op there."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Any constant works; it only has to be the same in every process and unlikely to collide with
# another application's advisory lock in the same database.
_SCHEMA_LOCK_KEY = 0x4D69_7275_5343_4845  # "MiruSCHE"


@asynccontextmanager
async def schema_lock(engine: AsyncEngine) -> AsyncGenerator[None]:
    if engine.dialect.name != "postgresql":
        yield
        return
    async with engine.connect() as conn, conn.begin():
        # Waiting for another process's provisioning can take longer than the 30 s statement
        # timeout (a first create_all, an index build); without this the waiters would fail with
        # QueryCanceled and crash their startup. LOCAL ends with the transaction, so it cannot
        # leak into whoever gets this pooled connection next, and the transaction-level advisory
        # lock below is released by the commit with nothing to forget to unlock.
        await conn.execute(text("SET LOCAL statement_timeout = 0"))
        await conn.execute(text("SET LOCAL idle_in_transaction_session_timeout = 0"))
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SCHEMA_LOCK_KEY})
        yield


__all__ = ["schema_lock"]
