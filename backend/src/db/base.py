from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import get_settings
from db.additive_upgrade import apply_additive_upgrade, pending_additive_steps
from db.postgres_search import ensure_postgres_search_index
from db.schema_lock import schema_lock


class Base(DeclarativeBase):
    """Declarative base for all ORM models (backend/src/db/).

    Dual-dialect conventions (database-schema.md, backend/CLAUDE.md §6): UUID PKs as
    app-generated `text`, JSON not JSONB where portable, CHECK constraints instead of native
    ENUM, money as integer micro-units. Applies to every model built on this base.
    """


def make_engine(database_url: str | None = None) -> AsyncEngine:
    settings = get_settings()
    url = database_url or settings.database_url
    if not url.startswith("postgresql"):
        # SQLite (dev/tests): its pool has different semantics and nothing to health-check.
        return create_async_engine(url, future=True)
    connect_args: dict[str, object] = {}
    if settings.db_statement_timeout_ms > 0:
        connect_args["server_settings"] = {
            "statement_timeout": str(settings.db_statement_timeout_ms)
        }
    return create_async_engine(
        url,
        future=True,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
        connect_args=connect_args,
    )


async def dispose_engine_for(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Close the pool behind a session factory at shutdown, so connections are returned to the
    server cleanly instead of being dropped when the process exits."""
    await session_factory.kw["bind"].dispose()


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


_SHOWN = 8  # how many missing items the startup error lists


class SchemaOutOfDateError(RuntimeError):
    """The database exists but is behind the models, and the mode is `strict`."""

    def __init__(self, behind: list[str]) -> None:
        shown = ", ".join(behind[:_SHOWN]) + (
            f" (+{len(behind) - _SHOWN} more)" if len(behind) > _SHOWN else ""
        )
        super().__init__(
            f"The database schema is behind the models: {shown}. In development, rebuild it "
            "with `uv run python -m db.reset --yes` (this empties the app database only, never "
            "the market data); or set SCHEMA_UPGRADE_MODE=additive to patch it in place."
        )
        self.behind = behind


async def ensure_schema(engine: AsyncEngine) -> None:
    """Create every table/index/constraint declared on `Base.metadata` that doesn't already
    exist, then add any column/index/unique key an existing table is missing — this project's sole
    schema-provisioning mechanism (no Alembic migration chain). Both steps are idempotent, so
    calling this once per process at startup is safe even when multiple replicas race to call it
    concurrently; each schema-bearing process (api, scheduler, ingest, mcp) calls it once from its
    own composition root."""
    # Side-effect import: registers every ORM model on `Base.metadata` before `create_all` runs —
    # deferred here (not a top-level import) to avoid a construction-time import cycle, since
    # `db.models` imports `Base` from this module.
    from db import models  # noqa: F401, PLC0415  # pyright: ignore[reportUnusedImport]

    async with schema_lock(engine):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        if get_settings().schema_upgrade_mode == "additive":
            # Existing tables keep their shape under `create_all`; add any column/index/unique
            # key the models gained since (additive only; see db/additive_upgrade.py).
            await apply_additive_upgrade(engine, Base.metadata)
        else:
            behind = await pending_additive_steps(engine, Base.metadata)
            if behind:
                raise SchemaOutOfDateError(behind)
        # Postgres-only extras the portable model cannot express (the chat-search trigram index).
        await ensure_postgres_search_index(engine)
        if get_settings().row_level_security:
            from db.rls import apply_policies  # noqa: PLC0415 - rls imports Base from this module

            await apply_policies(engine)


async def ensure_schema_for(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """`ensure_schema`, recovering the bound engine from an already-built session factory —
    the composition roots (`api`/`mcp`/`scheduler`/`ingest`) each build their session factory
    via the shared, sync `build_session_factory`/`make_session_factory` helpers and only have a
    running event loop to await this on afterward, at their own async startup point."""
    engine = session_factory.kw["bind"]
    await ensure_schema(engine)


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession]:
    """One transaction per request/unit of work; caller wraps with per-user WHERE scoping."""
    async with session_factory() as session:
        yield session
