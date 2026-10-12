"""Row-level security: a query that forgets `WHERE user_id = ?` cannot see another user's rows.

Postgres-only (set TEST_POSTGRES_URL); the SQLite half checks the no-op."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from db import (
    models,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    rls,
)
from db.base import Base
from db.models import ThreadRow, WatchlistRow
from tests.pg_support import app_role_url

pytestmark = pytest.mark.dual_dialect  # runs in the CI job that has a Postgres service

_PG_URL = os.environ.get("TEST_POSTGRES_URL")
needs_postgres = pytest.mark.skipif(not _PG_URL, reason="TEST_POSTGRES_URL not set")


@pytest.fixture
async def pg() -> AsyncIterator[AsyncEngine]:
    assert _PG_URL
    # Tables are created by the ordinary app role, so it owns them and FORCE applies to it; the
    # admin role only clears the previous test's tables, which it can as a superuser.
    admin = create_async_engine(_PG_URL)
    async with admin.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    engine = create_async_engine(await app_role_url(_PG_URL))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await rls.apply_policies(engine)
    rls.install(enabled=True)
    yield engine
    rls._user_id.set(None)  # leave no identity behind for the next test
    rls._system.set(False)
    await engine.dispose()
    async with admin.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await admin.dispose()


def _thread(user_id: str) -> ThreadRow:
    now = datetime.now(UTC)
    return ThreadRow(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title="t",
        created_at=now,
        updated_at=now,
        last_message_at=now,
    )


async def _seed(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with rls.system_access():
        async with factory() as session:
            session.add_all([_thread("alice"), _thread("alice"), _thread("bob")])
            await session.commit()


async def _count(engine: AsyncEngine, model: type) -> int:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


@needs_postgres
async def test_a_policy_guards_every_user_owned_table(pg: AsyncEngine) -> None:
    async with pg.connect() as conn:
        rows = (
            await conn.exec_driver_sql(
                "SELECT c.relname FROM pg_class c JOIN pg_policies p ON p.tablename = c.relname "
                "WHERE p.policyname = 'user_isolation' AND c.relrowsecurity "
                "AND c.relforcerowsecurity"
            )
        ).fetchall()

    assert sorted(r[0] for r in rows) == rls.rls_tables()
    assert "threads" in rls.rls_tables()
    assert "users" not in rls.rls_tables()  # sign-in looks users up before any identity exists


@needs_postgres
async def test_a_query_without_a_where_only_sees_the_acting_users_rows(pg: AsyncEngine) -> None:
    await _seed(pg)

    rls.bind_user("alice")
    assert await _count(pg, ThreadRow) == 2
    rls.bind_user("bob")
    assert await _count(pg, ThreadRow) == 1


@needs_postgres
async def test_with_no_identity_nothing_is_visible_and_nothing_can_be_written(
    pg: AsyncEngine,
) -> None:
    await _seed(pg)
    rls._user_id.set(None)

    assert await _count(pg, ThreadRow) == 0
    factory = async_sessionmaker(pg, expire_on_commit=False)
    async with factory() as session:
        session.add(_thread("alice"))
        with pytest.raises(DBAPIError):
            await session.commit()


@needs_postgres
async def test_a_user_cannot_write_a_row_for_someone_else(pg: AsyncEngine) -> None:
    rls.bind_user("alice")
    factory = async_sessionmaker(pg, expire_on_commit=False)

    async with factory() as session:
        session.add(_thread("alice"))
        await session.commit()  # their own row: fine
    async with factory() as session:
        session.add(_thread("bob"))
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.commit()


@needs_postgres
async def test_system_access_sees_everyone_and_the_identity_survives_a_commit(
    pg: AsyncEngine,
) -> None:
    await _seed(pg)
    with rls.system_access():
        assert await _count(pg, ThreadRow) == 3

    rls.bind_user("alice")
    factory = async_sessionmaker(pg, expire_on_commit=False)
    async with factory() as session:
        session.add(_thread("alice"))
        await session.commit()  # transaction 1 ends here ...
        rows = (await session.execute(select(ThreadRow))).scalars().all()  # ... transaction 2
    assert {r.user_id for r in rows} == {"alice"}
    assert len(rows) == 3


@needs_postgres
async def test_applying_the_policies_twice_is_harmless(pg: AsyncEngine) -> None:
    again = await rls.apply_policies(pg)

    assert again == rls.rls_tables()


@needs_postgres
async def test_other_user_tables_are_covered_too(pg: AsyncEngine) -> None:
    factory = async_sessionmaker(pg, expire_on_commit=False)
    now = datetime.now(UTC)
    with rls.system_access():
        async with factory() as session:
            session.add(
                WatchlistRow(
                    id=str(uuid.uuid4()),
                    user_id="bob",
                    name="w",
                    market="TW",
                    asset_class="stock",
                    sort_order="a0",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    rls.bind_user("alice")
    assert await _count(pg, WatchlistRow) == 0


@needs_postgres
async def test_a_superuser_connection_is_rejected_instead_of_silently_unprotected() -> None:
    assert _PG_URL
    admin = create_async_engine(_PG_URL)  # the container's own role: a superuser
    try:
        with pytest.raises(rls.RlsNotEnforcedError, match="superuser or has BYPASSRLS"):
            await rls.assert_enforced(admin)
    finally:
        await admin.dispose()


async def test_it_is_a_no_op_on_sqlite() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        assert await rls.apply_policies(engine) == []
    finally:
        await engine.dispose()
