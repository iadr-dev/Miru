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
from db.additive_upgrade import apply_additive_upgrade


class Base(DeclarativeBase):
    """Declarative base for all ORM models (backend/src/db/).

    Dual-dialect conventions (database-schema.md, backend/CLAUDE.md §6): UUID PKs as
    app-generated `text`, JSON not JSONB where portable, CHECK constraints instead of native
    ENUM, money as integer micro-units. Applies to every model built on this base.
    """


def make_engine(database_url: str | None = None) -> AsyncEngine:
    url = database_url or get_settings().database_url
    return create_async_engine(url, future=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


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

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Existing tables keep their shape under `create_all`; add any column/index/unique key the
    # models gained since (additive only — never drops or rewrites; see db/additive_upgrade.py).
    await apply_additive_upgrade(engine, Base.metadata)


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
