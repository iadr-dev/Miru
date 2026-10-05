"""The startup upgrade adds what an older database lacks and nothing else: data stays, nothing is
dropped, and a current database is left alone."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Column, Index, Integer, MetaData, String, Table, UniqueConstraint, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from db.additive_upgrade import apply_additive_upgrade


@pytest.fixture
async def engine(tmp_path: Path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with eng.begin() as conn:
        # The database as an earlier model version left it: no `email`, `plan`, index or unique key,
        # plus a column (`legacy`) the current model no longer has.
        await conn.exec_driver_sql(
            "CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL, legacy TEXT)"
        )
        await conn.exec_driver_sql(
            "INSERT INTO accounts (id, name, legacy) VALUES ('a1', 'Ann', 'keep-me')"
        )
    yield eng
    await eng.dispose()


def _current_model() -> MetaData:
    metadata = MetaData()
    Table(
        "accounts",
        metadata,
        Column("id", String, primary_key=True),
        Column("name", String, nullable=False),
        Column("email", String, nullable=True),
        Column("plan", String, nullable=False, server_default="free"),
        Column("seats", Integer, nullable=False),  # NOT NULL, no default -> added nullable
        Index("ix_accounts_name", "name"),
        UniqueConstraint("email", name="uq_accounts_email"),
    )
    Table("brand_new", metadata, Column("id", String, primary_key=True))  # create_all's job
    return metadata


async def _columns(engine: AsyncEngine) -> dict[str, bool]:
    async with engine.connect() as conn:
        cols = await conn.run_sync(lambda c: inspect(c).get_columns("accounts"))
    return {c["name"]: bool(c["nullable"]) for c in cols}


async def test_adds_missing_columns_index_and_unique_and_keeps_the_data(engine: AsyncEngine):
    applied = await apply_additive_upgrade(engine, _current_model())

    assert set(applied) == {
        "column accounts.email",
        "column accounts.plan",
        "column accounts.seats",
        "index accounts.ix_accounts_name",
        "unique accounts.uq_accounts_email",
    }
    cols = await _columns(engine)
    assert {"email", "plan", "seats"} <= set(cols)
    assert cols["plan"] is False  # has a server default, so NOT NULL is safe for existing rows
    assert cols["seats"] is True  # no default: must not break existing rows
    async with engine.connect() as conn:
        row = (await conn.exec_driver_sql("SELECT id, name, legacy, plan FROM accounts")).one()
        indexes = await conn.run_sync(
            lambda c: {i["name"] for i in inspect(c).get_indexes("accounts")}
        )
    assert tuple(row) == ("a1", "Ann", "keep-me", "free")  # existing data + default backfill
    assert {"ix_accounts_name", "uq_accounts_email"} <= indexes


async def test_never_drops_or_creates_whole_tables(engine: AsyncEngine):
    await apply_additive_upgrade(engine, _current_model())
    assert "legacy" in await _columns(engine)  # a column the model lost is left alone
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert "brand_new" not in tables  # whole tables are create_all's job


async def test_is_idempotent_and_a_current_database_is_untouched(engine: AsyncEngine):
    model = _current_model()
    assert await apply_additive_upgrade(engine, model)
    assert await apply_additive_upgrade(engine, model) == []


async def test_the_added_unique_key_is_enforced(engine: AsyncEngine):
    await apply_additive_upgrade(engine, _current_model())
    async with engine.begin() as conn:
        await conn.exec_driver_sql("INSERT INTO accounts (id, name, email) VALUES ('a2','B','x@y')")
    with pytest.raises(Exception, match="UNIQUE"):
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "INSERT INTO accounts (id, name, email) VALUES ('a3','C','x@y')"
            )


async def test_an_unique_key_existing_rows_violate_is_skipped_not_fatal(tmp_path: Path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dupes.db'}")
    async with eng.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE accounts (id TEXT PRIMARY KEY, email TEXT)")
        await conn.exec_driver_sql("INSERT INTO accounts VALUES ('1','same'), ('2','same')")
    metadata = MetaData()
    Table(
        "accounts",
        metadata,
        Column("id", String, primary_key=True),
        Column("email", String),
        UniqueConstraint("email", name="uq_accounts_email"),
    )
    try:
        assert await apply_additive_upgrade(eng, metadata) == []  # logged, service still starts
    finally:
        await eng.dispose()
