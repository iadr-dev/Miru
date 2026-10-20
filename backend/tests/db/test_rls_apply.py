"""`apply_policies` changes only what differs, and never waits behind an open transaction when
nothing needs changing.

The incident: every process start ran four `ALTER`/`POLICY` statements on every user-owned table
even when nothing had changed. They need exclusive locks, so a session left idle inside a
transaction made the start-up queue (and block every later query on those tables) until the
statement timeout killed it."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from db import (
    models,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    rls,
)
from db.base import Base
from tests.pg_support import app_role_url

pytestmark = pytest.mark.concurrency

_PG_URL = os.environ.get("TEST_POSTGRES_URL")
_DDL_PREFIXES = ("ALTER", "DROP", "CREATE", "COMMENT")


@pytest.fixture
async def pg() -> AsyncIterator[AsyncEngine]:
    if not _PG_URL:
        pytest.skip("TEST_POSTGRES_URL not set - Postgres concurrency check skipped")
    admin = create_async_engine(_PG_URL)
    async with admin.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    engine = create_async_engine(await app_role_url(_PG_URL))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()
    async with admin.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await admin.dispose()


class _Ddl:
    """Counts the DDL statements an engine sends."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.statements: list[str] = []
        event.listen(engine.sync_engine, "before_cursor_execute", self._record)

    def _record(self, _conn: object, _cur: object, statement: str, *_rest: object) -> None:
        if statement.lstrip().upper().startswith(_DDL_PREFIXES):
            self.statements.append(statement)

    def reset(self) -> None:
        self.statements.clear()


async def test_the_first_apply_covers_every_table_and_the_second_sends_no_ddl(
    pg: AsyncEngine,
) -> None:
    ddl = _Ddl(pg)
    tables = await rls.apply_policies(pg)
    assert len(ddl.statements) == len(tables) * 5  # enable, force, drop, create, comment

    ddl.reset()
    assert await rls.apply_policies(pg) == tables
    assert ddl.statements == []  # nothing differs: no DDL, therefore no lock


async def test_only_the_table_that_drifted_is_repaired(pg: AsyncEngine) -> None:
    await rls.apply_policies(pg)
    ddl = _Ddl(pg)
    async with pg.begin() as conn:
        await conn.execute(text('DROP POLICY user_isolation ON "threads"'))

    await rls.apply_policies(pg)

    assert len(ddl.statements) == 1 + 5  # the drop above, then the five that repair one table
    async with pg.connect() as conn:
        found = (
            await conn.execute(text("SELECT count(*) FROM pg_policies WHERE tablename = 'threads'"))
        ).scalar()
    assert found == 1


async def test_switching_row_level_security_off_on_a_table_is_noticed(pg: AsyncEngine) -> None:
    await rls.apply_policies(pg)
    async with pg.begin() as conn:
        await conn.execute(text('ALTER TABLE "threads" NO FORCE ROW LEVEL SECURITY'))
    ddl = _Ddl(pg)

    await rls.apply_policies(pg)

    assert len(ddl.statements) == 5
    async with pg.connect() as conn:
        forced = (
            await conn.execute(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'threads'")
            )
        ).scalar()
    assert forced is True


async def test_a_policy_with_an_outdated_definition_is_replaced(pg: AsyncEngine) -> None:
    await rls.apply_policies(pg)
    async with pg.begin() as conn:
        await conn.execute(
            text("COMMENT ON POLICY user_isolation ON \"threads\" IS 'miru-rls:old'")
        )
    ddl = _Ddl(pg)

    await rls.apply_policies(pg)

    assert len(ddl.statements) == 5


async def test_an_up_to_date_apply_does_not_wait_behind_an_open_transaction(
    pg: AsyncEngine,
) -> None:
    await rls.apply_policies(pg)
    stuck = await pg.connect()
    try:
        await stuck.execute(text('SELECT count(*) FROM "threads"'))  # idle in transaction
        await stuck.execute(text('SELECT count(*) FROM "users"'))
        await asyncio.wait_for(rls.apply_policies(pg), timeout=5)  # the operator's incident
    finally:
        await stuck.rollback()
        await stuck.close()


async def test_a_table_that_stays_locked_fails_clearly_and_is_left_unchanged(
    pg: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rls.apply_policies(pg)
    async with pg.begin() as conn:
        await conn.execute(text('DROP POLICY user_isolation ON "threads"'))
    monkeypatch.setattr(rls, "_DDL_LOCK_TIMEOUT_MS", 300)
    monkeypatch.setattr(rls, "_DDL_ATTEMPTS", 2)

    stuck = await pg.connect()
    try:
        await stuck.execute(text('SELECT count(*) FROM "threads"'))  # holds ACCESS SHARE
        applying = rls.apply_policies(pg)
        with pytest.raises(rls.RlsPolicyBlockedError, match="threads"):
            await asyncio.wait_for(applying, timeout=20)
    finally:
        await stuck.rollback()
        await stuck.close()

    async with pg.connect() as conn:  # the failed attempt rolled back; the table is not half-done
        policies = (
            await conn.execute(text("SELECT count(*) FROM pg_policies WHERE tablename = 'threads'"))
        ).scalar()
    assert policies == 0
    await rls.apply_policies(pg)  # and it applies as soon as the blocker is gone
    async with pg.connect() as conn:
        again = (
            await conn.execute(text("SELECT count(*) FROM pg_policies WHERE tablename = 'threads'"))
        ).scalar()
    assert again == 1
