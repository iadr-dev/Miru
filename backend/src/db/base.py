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


class Base(DeclarativeBase):
    """Declarative base for all ORM models (backend/src/db/).

    Dual-dialect conventions (SQLite dev / Postgres prod): UUID PKs as app-generated `text`,
    JSON not JSONB where portable, CHECK constraints instead of native ENUM, money as integer
    micro-units. Applies to every model built on this base.
    """


def make_engine(database_url: str | None = None) -> AsyncEngine:
    url = database_url or get_settings().database_url
    return create_async_engine(url, future=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession]:
    """One transaction per request/unit of work; caller wraps with per-user WHERE scoping."""
    async with session_factory() as session:
        yield session
