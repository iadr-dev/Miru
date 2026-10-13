"""Connection hygiene on a real Postgres: bounded waits, no immortal idle transactions, a schema
lock that outlasts the statement timeout, and health probes that do not share the app pool."""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

import db.base as db_base
from api.routers.probes import _check_postgres
from config.settings import Settings
from db.schema_lock import schema_lock

pytestmark = pytest.mark.concurrency


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set - Postgres concurrency check skipped")
    return url


def _settings(url: str, **overrides: object) -> Settings:
    return Settings(jwt_secret_key="x" * 40, env="development", database_url=url, **overrides)  # type: ignore[arg-type]


def _engine(monkeypatch: pytest.MonkeyPatch, url: str, **overrides: object):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(db_base, "get_settings", lambda: _settings(url, **overrides))
    return db_base.make_engine(url)


async def test_the_engine_applies_the_idle_transaction_and_statement_timeouts(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(
        monkeypatch, pg_url, db_idle_in_transaction_timeout_ms=7000, db_statement_timeout_ms=9000
    )
    try:
        async with engine.connect() as conn:
            idle = (await conn.execute(text("SHOW idle_in_transaction_session_timeout"))).scalar()
            statement = (await conn.execute(text("SHOW statement_timeout"))).scalar()
        assert (idle, statement) == ("7s", "9s")
    finally:
        await engine.dispose()


async def test_a_session_left_idle_in_a_transaction_is_ended_by_the_server(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(monkeypatch, pg_url, db_idle_in_transaction_timeout_ms=1000)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))  # autobegin: now idle in a transaction
            await asyncio.sleep(1.8)
            with pytest.raises(DBAPIError):
                await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


async def test_waiting_for_a_pooled_connection_fails_fast(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(
        monkeypatch, pg_url, db_pool_size=1, db_max_overflow=0, db_pool_timeout_seconds=1
    )
    try:
        async with engine.connect():
            started = time.monotonic()
            with pytest.raises(PoolTimeoutError):
                async with engine.connect():
                    pass
            assert time.monotonic() - started < 5
    finally:
        await engine.dispose()


async def test_a_slow_schema_lock_holder_does_not_crash_the_processes_waiting_behind_it(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Statement timeout 1 s; the first holder keeps the lock for 2.5 s. Before the fix the waiter's
    # blocking `pg_advisory_lock` was cancelled by the statement timeout and startup crashed.
    engine = _engine(monkeypatch, pg_url, db_statement_timeout_ms=1000)
    order: list[str] = []

    async def holder() -> None:
        async with schema_lock(engine):
            order.append("holder has it")
            await asyncio.sleep(2.5)
            order.append("holder done")

    async def waiter() -> None:
        await asyncio.sleep(0.3)
        async with schema_lock(engine):
            order.append("waiter has it")

    try:
        await asyncio.gather(holder(), waiter())
        assert order == ["holder has it", "holder done", "waiter has it"]
    finally:
        await engine.dispose()


async def test_the_schema_lock_does_not_leak_its_settings_to_the_next_user_of_the_connection(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(monkeypatch, pg_url, db_statement_timeout_ms=4000, db_pool_size=1)
    try:
        async with schema_lock(engine):
            pass
        async with engine.connect() as conn:  # the same pooled connection, handed out again
            assert (await conn.execute(text("SHOW statement_timeout"))).scalar() == "4s"
    finally:
        await engine.dispose()


async def test_the_probe_answers_even_when_the_application_pool_is_exhausted(pg_url: str) -> None:
    def exhausted_pool(*_a: object, **_k: object) -> object:
        raise PoolTimeoutError("QueuePool limit reached")

    state = SimpleNamespace(settings=_settings(pg_url), session_factory=exhausted_pool)
    assert await _check_postgres(state) is True  # type: ignore[arg-type]


async def test_the_probe_reports_a_database_that_is_really_down() -> None:
    state = SimpleNamespace(
        settings=_settings("postgresql+asyncpg://nobody:nope@127.0.0.1:1/none"),
        session_factory=None,
    )
    assert await _check_postgres(state) is False  # type: ignore[arg-type]
