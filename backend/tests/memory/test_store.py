"""`SqlAlchemyMemoryStore` — upsert/list/delete, and the write-path governance refusal
(credentials/raw payloads/trading language, and the retired `watchlist` write target)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from memory.models import MemoryGovernanceError, MemoryScope, MemoryType
from memory.store import SqlAlchemyMemoryStore


async def test_upsert_then_list_round_trips(db_session: AsyncSession) -> None:
    store = SqlAlchemyMemoryStore(db_session)

    written = await store.upsert(
        user_id="user-1",
        scope=MemoryScope.USER,
        type=MemoryType.PREFERENCE,
        key="verbosity",
        content={"level": "concise"},
    )
    listed = await store.list(user_id="user-1")

    assert written.key == "verbosity"
    assert len(listed) == 1
    assert listed[0].content == {"level": "concise"}


async def test_upsert_with_same_key_updates_in_place(db_session: AsyncSession) -> None:
    store = SqlAlchemyMemoryStore(db_session)
    await store.upsert(
        user_id="user-1",
        scope=MemoryScope.USER,
        type=MemoryType.PREFERENCE,
        key="verbosity",
        content={"level": "concise"},
    )
    await store.upsert(
        user_id="user-1",
        scope=MemoryScope.USER,
        type=MemoryType.PREFERENCE,
        key="verbosity",
        content={"level": "verbose"},
    )

    listed = await store.list(user_id="user-1")

    assert len(listed) == 1
    assert listed[0].content == {"level": "verbose"}


async def test_delete_removes_the_row_and_is_idempotent(db_session: AsyncSession) -> None:
    store = SqlAlchemyMemoryStore(db_session)
    written = await store.upsert(
        user_id="user-1",
        scope=MemoryScope.USER,
        type=MemoryType.FINDING,
        key="2330-flow-note",
        content={"observation": "8-day foreign net-buy streak"},
    )

    first_delete = await store.delete(user_id="user-1", memory_id=written.id)
    second_delete = await store.delete(user_id="user-1", memory_id=written.id)

    assert first_delete is True
    assert second_delete is False
    assert await store.list(user_id="user-1") == []


async def test_upsert_refuses_watchlist_type(db_session: AsyncSession) -> None:
    store = SqlAlchemyMemoryStore(db_session)

    with pytest.raises(MemoryGovernanceError):
        await store.upsert(
            user_id="user-1",
            scope=MemoryScope.USER,
            type=MemoryType.WATCHLIST,
            key="core",
            content={"symbols": ["2330"]},
        )


async def test_upsert_refuses_credential_shaped_content(db_session: AsyncSession) -> None:
    store = SqlAlchemyMemoryStore(db_session)

    with pytest.raises(MemoryGovernanceError):
        await store.upsert(
            user_id="user-1",
            scope=MemoryScope.USER,
            type=MemoryType.PREFERENCE,
            key="oops",
            content={"api_key": "sk-leaked"},
        )


async def test_list_scopes_by_user(db_session: AsyncSession) -> None:
    store = SqlAlchemyMemoryStore(db_session)
    await store.upsert(
        user_id="user-1",
        scope=MemoryScope.USER,
        type=MemoryType.PREFERENCE,
        key="verbosity",
        content={"level": "concise"},
    )
    await store.upsert(
        user_id="user-2",
        scope=MemoryScope.USER,
        type=MemoryType.PREFERENCE,
        key="verbosity",
        content={"level": "verbose"},
    )

    listed = await store.list(user_id="user-1")

    assert len(listed) == 1
    assert listed[0].user_id == "user-1"
