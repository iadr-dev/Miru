"""Characterization of `db.additive_upgrade._plan`: the exact steps, their order and their SQL."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from db.additive_upgrade import _plan


@pytest.fixture
async def engine(tmp_path: Path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with eng.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL, legacy TEXT)"
        )
    yield eng
    await eng.dispose()


async def _plan_steps(eng: AsyncEngine, metadata: MetaData) -> list[tuple[str, str, bool]]:
    async with eng.connect() as conn:
        steps = await conn.run_sync(_plan, metadata)
    return [(s.description, s.sql, s.is_column) for s in steps]


def _current_model() -> MetaData:
    metadata = MetaData()
    Table(
        "accounts",
        metadata,
        Column("id", String, primary_key=True),
        Column("name", String, nullable=False),
        Column("email", String, nullable=True),
        Column("plan", String, nullable=False, server_default="free"),
        Column("seats", Integer, nullable=False),
        Index("ix_accounts_name", "name"),
        UniqueConstraint("email", name="uq_accounts_email"),
    )
    Table("brand_new", metadata, Column("id", String, primary_key=True))
    return metadata


async def test_plan_orders_columns_first_and_emits_exact_sql(engine: AsyncEngine) -> None:
    plan = await _plan_steps(engine, _current_model())

    assert [(d, c) for d, _, c in plan] == [
        ("column accounts.email", True),
        ("column accounts.plan", True),
        ("column accounts.seats", True),
        ("index accounts.ix_accounts_name", False),
        ("unique accounts.uq_accounts_email", False),
    ]
    sql = {d: s for d, s, _ in plan}
    assert sql["column accounts.email"] == "ALTER TABLE accounts ADD COLUMN email VARCHAR"
    assert sql["column accounts.plan"] == (
        "ALTER TABLE accounts ADD COLUMN \"plan\" VARCHAR DEFAULT 'free' NOT NULL"
    )
    assert sql["column accounts.seats"] == "ALTER TABLE accounts ADD COLUMN seats INTEGER"
    assert (
        "CREATE INDEX IF NOT EXISTS ix_accounts_name ON accounts (name)"
        in (sql["index accounts.ix_accounts_name"])
    )
    # SQLite cannot ADD CONSTRAINT, so the unique key becomes an equivalent unique index.
    assert sql["unique accounts.uq_accounts_email"] == (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_email ON accounts (email)"
    )


async def test_plan_adds_a_unique_index_for_a_new_unique_column(engine: AsyncEngine) -> None:
    metadata = MetaData()
    Table(
        "accounts",
        metadata,
        Column("id", String, primary_key=True),
        Column("name", String, nullable=False),
        Column("legacy", String),
        Column("handle", String, unique=True),
        Column("nick", String),
    )
    plan = await _plan_steps(engine, metadata)

    assert [(d, c) for d, _, c in plan] == [
        ("column accounts.handle", True),
        ("column accounts.nick", True),
        ("unique accounts.uq_accounts_handle", False),
    ]
    assert plan[2][1] == "CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_handle ON accounts (handle)"


async def test_plan_skips_what_is_present_unnamed_or_not_unique(tmp_path: Path) -> None:
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'current.db'}")
    try:
        async with eng.begin() as conn:
            await conn.exec_driver_sql(
                "CREATE TABLE accounts (id TEXT PRIMARY KEY, name TEXT, email TEXT, code TEXT, "
                "CONSTRAINT uq_accounts_email UNIQUE (email))"
            )
            await conn.exec_driver_sql("CREATE INDEX ix_accounts_name ON accounts (name)")
            await conn.exec_driver_sql("CREATE UNIQUE INDEX uq_accounts_code ON accounts (code)")
        metadata = MetaData()
        Table(
            "accounts",
            metadata,
            Column("id", String, primary_key=True),
            Column("name", String),
            Column("email", String),
            Column("code", String),
            Index("ix_accounts_name", "name"),  # live index name -> skipped
            UniqueConstraint("email", name="uq_accounts_email"),  # live unique constraint
            UniqueConstraint("code", name="uq_accounts_code"),  # live as an index name
            UniqueConstraint("id", "name"),  # unnamed -> skipped
            CheckConstraint("length(name) > 0", name="ck_accounts_name"),  # not unique -> skipped
        )
        Table("brand_new", metadata, Column("id", String, primary_key=True))
        assert await _plan_steps(eng, metadata) == []
    finally:
        await eng.dispose()
