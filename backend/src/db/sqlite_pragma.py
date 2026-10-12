from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import ConnectionPoolEntry


def enable_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """SQLite ignores FKs by default — ON DELETE CASCADE silently no-ops without this.
    No-op on non-SQLite dialects."""
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(  # pyright: ignore[reportUnusedFunction] — registered via decorator
        dbapi_connection: DBAPIConnection, _connection_record: ConnectionPoolEntry
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
