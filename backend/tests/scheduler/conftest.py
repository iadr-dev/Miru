"""Fixtures for `tests/scheduler/`: an in-memory SQLite session + factory, a real user-row
inserter, and the deterministic stub orchestrator/router/channels the worker runs against — no
live network, matching the default-profile convention."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.models import UserRow
from db.sqlite_pragma import enable_sqlite_foreign_keys
from schemas.enums import Tier


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(eng)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def insert_user(session_factory: async_sessionmaker[AsyncSession], *, tier: Tier) -> str:
    now = datetime.now(UTC)
    user_id = str(uuid.uuid4())
    async with session_factory() as session:
        session.add(
            UserRow(
                id=user_id,
                email=f"{user_id}@example.test",
                display_name="Test",
                tier=tier.value,
                personality_id="steady",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return user_id
