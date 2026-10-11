"""Shared pieces for the Postgres-only concurrency tests (see the `pg_session_factory` fixture in
`tests/conftest.py`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import UserRow


async def insert_user(factory: async_sessionmaker[object]) -> str:
    """A committed user row, returning its id."""
    now = datetime.now(UTC)
    user_id = str(uuid.uuid4())
    async with factory() as session:  # type: ignore[union-attr]
        session.add(
            UserRow(
                id=user_id,
                email=f"{user_id}@example.test",
                display_name="Race",
                tier="free",
                personality_id="steady",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return user_id
