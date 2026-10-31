"""Create a local test user in the scratch SQLite DB and print an access JWT for it.

Run from the SCRATCH directory with the same DATABASE_URL and JWT_SECRET_KEY as the API:

    uv run --project <repo>/backend python <repo>/.claude/skills/verify/scripts/mint_token.py [tier]

`tier` is free (default), premium or pro. There is no dev login route, so this is the only way to
authenticate against `/agent/query`, `/me` and the web app (send it as `Authorization: Bearer ...`
or as the `access_token` cookie on host `localhost`).
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from accounts.auth import issue_access_token
from config.settings import get_settings
from db import models  # noqa: F401  registers ORM models onto Base.metadata
from db.base import Base
from db.models import UserRow
from db.sqlite_pragma import enable_sqlite_foreign_keys


async def main(tier: str) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    user_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
        session.add(
            UserRow(
                id=user_id,
                email=f"verify-{user_id[:8]}@example.invalid",
                display_name="Verify",
                tier=tier,
                personality_id="steady",
                created_at=now,
                updated_at=now,
            )
        )
    await engine.dispose()
    print(issue_access_token(user_id=user_id, settings=settings))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "free"))
