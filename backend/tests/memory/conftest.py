"""Shared async SQLite session fixture for `memory/`'s store tests. Each test gets a fresh
in-memory database with every table created from `Base.metadata`, so these tests never depend
on the on-disk dev DB or a prior Alembic migration run."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Side-effect import: registers every ORM model on Base.metadata so create_all sees the full
# schema.
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()
