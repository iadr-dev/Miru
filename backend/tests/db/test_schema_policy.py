"""Development keeps the schema fresh instead of patching it: a stale database is refused with the
way out, `additive` mode still patches, and the reset command rebuilds the app database only."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from db.base import SchemaOutOfDateError, ensure_schema
from db.reset import main as reset_main
from db.schema_lock import schema_lock


@pytest.fixture
def sqlite_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'app.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("SCHEMA_UPGRADE_MODE", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    return url


async def _make_stale(url: str) -> None:
    engine = create_async_engine(url)
    try:
        await ensure_schema(engine)
        async with engine.begin() as conn:
            # An older shape: the table exists but lacks a column the model has.
            await conn.exec_driver_sql("ALTER TABLE threads DROP COLUMN archived")
    finally:
        await engine.dispose()


async def _columns(url: str, table: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda c: {col["name"] for col in inspect(c).get_columns(table)}
            )
    finally:
        await engine.dispose()


async def test_a_fresh_database_is_created_without_any_alter(sqlite_url: str) -> None:
    engine = create_async_engine(sqlite_url)
    try:
        await ensure_schema(engine)
        await ensure_schema(engine)  # a second start is a no-op
    finally:
        await engine.dispose()

    assert "archived" in await _columns(sqlite_url, "threads")


async def test_strict_mode_refuses_a_stale_database_and_says_how_to_reset(sqlite_url: str) -> None:
    await _make_stale(sqlite_url)
    engine = create_async_engine(sqlite_url)
    try:
        with pytest.raises(SchemaOutOfDateError) as raised:
            await ensure_schema(engine)
    finally:
        await engine.dispose()

    assert "column threads.archived" in str(raised.value)
    assert "db.reset" in str(raised.value)
    assert "archived" not in await _columns(sqlite_url, "threads")  # nothing was patched


async def test_additive_mode_patches_in_place(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _make_stale(sqlite_url)
    monkeypatch.setenv("SCHEMA_UPGRADE_MODE", "additive")
    engine = create_async_engine(sqlite_url)
    try:
        await ensure_schema(engine)
    finally:
        await engine.dispose()

    assert "archived" in await _columns(sqlite_url, "threads")


def test_reset_rebuilds_a_stale_database(sqlite_url: str) -> None:
    asyncio.run(_make_stale(sqlite_url))

    assert reset_main(["--yes"]) == 0

    assert "archived" in asyncio.run(_columns(sqlite_url, "threads"))


def test_reset_needs_confirmation_and_refuses_outside_development(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert reset_main([]) == 2
    assert "--yes" in capsys.readouterr().err

    monkeypatch.setenv("ENV", "production")
    assert reset_main(["--yes"]) == 2
    assert "production" in capsys.readouterr().err


async def test_the_schema_lock_is_a_no_op_on_sqlite(sqlite_url: str) -> None:
    engine = create_async_engine(sqlite_url)
    try:
        async with schema_lock(engine):
            pass
    finally:
        await engine.dispose()
