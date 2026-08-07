"""Async SQLite session + user helper for pet-store tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from db.base import Base
from db.models import UserRow
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


async def make_user(session: AsyncSession) -> str:
    now = datetime.now(UTC)
    row = UserRow(
        id=str(uuid.uuid4()),
        display_name="Test",
        tier="free",
        personality_id="steady",
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    return row.id
