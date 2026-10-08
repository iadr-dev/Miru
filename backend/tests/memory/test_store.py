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
        content={"focus": "etf"},
    )
    listed = await store.list(user_id="user-1")

    assert written.key == "verbosity"
    assert len(listed) == 1
    assert listed[0].content == {"focus": "etf"}


async def test_upsert_with_same_key_updates_in_place(db_session: AsyncSession) -> None:
    store = SqlAlchemyMemoryStore(db_session)
    await store.upsert(
        user_id="user-1",
        scope=MemoryScope.USER,
        type=MemoryType.PREFERENCE,
        key="verbosity",
        content={"focus": "etf"},
    )
    await store.upsert(
        user_id="user-1",
        scope=MemoryScope.USER,
        type=MemoryType.PREFERENCE,
        key="verbosity",
        content={"focus": "stocks"},
    )

    listed = await store.list(user_id="user-1")

    assert len(listed) == 1
    assert listed[0].content == {"focus": "stocks"}


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
        content={"focus": "etf"},
    )
    await store.upsert(
        user_id="user-2",
        scope=MemoryScope.USER,
        type=MemoryType.PREFERENCE,
        key="verbosity",
        content={"focus": "stocks"},
    )

    listed = await store.list(user_id="user-1")

    assert len(listed) == 1
    assert listed[0].user_id == "user-1"


async def test_mark_used_stamps_the_time_and_counts_each_use(db_session) -> None:
    store = SqlAlchemyMemoryStore(db_session)
    memory = await store.upsert(
        user_id="u1",
        scope=MemoryScope.USER,
        type=MemoryType.PREFERENCE,
        key="focus",
        content={"text": "高股息 ETF"},
    )
    assert memory.last_used_at is None
    assert memory.use_count == 0
    await store.mark_used(user_id="u1", memory_ids=[memory.id])
    await store.mark_used(user_id="u1", memory_ids=[memory.id])
    (again,) = await store.list(user_id="u1")
    assert again.last_used_at is not None
    assert again.use_count == 2


async def test_mark_used_ignores_another_users_memory(db_session) -> None:
    store = SqlAlchemyMemoryStore(db_session)
    memory = await store.upsert(
        user_id="u1",
        scope=MemoryScope.USER,
        type=MemoryType.FINDING,
        key="k",
        content={"text": "x"},
    )
    await store.mark_used(user_id="someone-else", memory_ids=[memory.id])
    (same,) = await store.list(user_id="u1")
    assert same.use_count == 0


async def test_a_preference_that_is_a_style_instruction_is_refused(db_session) -> None:
    store = SqlAlchemyMemoryStore(db_session)
    with pytest.raises(MemoryGovernanceError, match="Tone settings"):
        await store.upsert(
            user_id="u1",
            scope=MemoryScope.USER,
            type=MemoryType.PREFERENCE,
            key="style",
            content={"text": "always end with a one-line TL;DR"},
        )
    # a note may legitimately mention tone (e.g. an earnings-call observation)
    await store.upsert(
        user_id="u1",
        scope=MemoryScope.USER,
        type=MemoryType.FINDING,
        key="call",
        content={"text": "管理層語氣偏保守"},
    )


async def test_editing_a_preference_into_a_style_instruction_is_refused(db_session) -> None:
    store = SqlAlchemyMemoryStore(db_session)
    memory = await store.upsert(
        user_id="u1",
        scope=MemoryScope.USER,
        type=MemoryType.PREFERENCE,
        key="focus",
        content={"text": "高股息 ETF"},
    )
    with pytest.raises(MemoryGovernanceError, match="Tone settings"):
        await store.update(user_id="u1", memory_id=memory.id, content={"text": "請用條列式"})


async def test_a_secret_written_into_free_text_is_refused(db_session) -> None:
    store = SqlAlchemyMemoryStore(db_session)
    with pytest.raises(MemoryGovernanceError, match="credential"):
        await store.upsert(
            user_id="u1",
            scope=MemoryScope.USER,
            type=MemoryType.FINDING,
            key="note",
            content={"text": "my key is sk-abcdef123456789"},
        )
