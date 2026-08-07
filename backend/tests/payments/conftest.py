"""Fixtures for `tests/payments/`: an in-memory SQLite session, a user-row inserter, and a
Settings instance carrying the 8 test price IDs + a webhook secret — no live Paddle calls."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import Settings
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.models import UserRow
from db.sqlite_pragma import enable_sqlite_foreign_keys
from schemas.enums import Tier

WEBHOOK_SECRET = "whsec-test-not-a-real-secret"

# Distinct, recognizable test price IDs for each of the 8 plans.
PRICE_PREMIUM_MONTHLY = "pri_premium_monthly"
PRICE_PREMIUM_ANNUAL = "pri_premium_annual"
PRICE_PREMIUM_BYOK_MONTHLY = "pri_premium_byok_monthly"
PRICE_PREMIUM_BYOK_ANNUAL = "pri_premium_byok_annual"
PRICE_PRO_MONTHLY = "pri_pro_monthly"
PRICE_PRO_ANNUAL = "pri_pro_annual"
PRICE_PRO_BYOK_MONTHLY = "pri_pro_byok_monthly"
PRICE_PRO_BYOK_ANNUAL = "pri_pro_byok_annual"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        paddle_api_key="pk-test",
        paddle_webhook_secret=WEBHOOK_SECRET,
        paddle_client_token="ptok-test-publishable",
        paddle_environment="sandbox",
        paddle_price_premium_monthly=PRICE_PREMIUM_MONTHLY,
        paddle_price_premium_annual=PRICE_PREMIUM_ANNUAL,
        paddle_price_premium_byok_monthly=PRICE_PREMIUM_BYOK_MONTHLY,
        paddle_price_premium_byok_annual=PRICE_PREMIUM_BYOK_ANNUAL,
        paddle_price_pro_monthly=PRICE_PRO_MONTHLY,
        paddle_price_pro_annual=PRICE_PRO_ANNUAL,
        paddle_price_pro_byok_monthly=PRICE_PRO_BYOK_MONTHLY,
        paddle_price_pro_byok_annual=PRICE_PRO_BYOK_ANNUAL,
    )


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


async def make_user(session: AsyncSession, *, tier: Tier = Tier.FREE) -> str:
    now = datetime.now(UTC)
    row = UserRow(
        id=str(uuid.uuid4()),
        email=None,
        display_name="Test",
        tier=tier.value,
        personality_id="steady",
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    return row.id
