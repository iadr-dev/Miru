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


APP_ROLE = "rls_app"


async def app_role_url(admin_url: str) -> str:
    """URL for an ordinary (non-superuser, non-BYPASSRLS) role on the same database.

    Row-level security does not apply to superusers, and the role a test container is created with
    is one, so RLS tests connect as this role instead. It owns the tables it creates, which
    FORCE ROW LEVEL SECURITY then subjects to the policies."""
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.engine import make_url  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    url = make_url(admin_url)
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = (
                await conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
                )
            ).scalar()
            if not exists:
                await conn.execute(
                    text(
                        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE}' "
                        "NOSUPERUSER NOBYPASSRLS"
                    )
                )
            await conn.execute(text(f'GRANT ALL ON DATABASE "{url.database}" TO {APP_ROLE}'))
            await conn.execute(text(f"GRANT ALL ON SCHEMA public TO {APP_ROLE}"))
    finally:
        await admin.dispose()
    return url.set(username=APP_ROLE, password=APP_ROLE).render_as_string(hide_password=False)
