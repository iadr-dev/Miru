"""`SqlAlchemyConversationStore` — create/list/get/append/fork/switch_branch/delete, and the
tree-branching + active-path-only invariant."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from conversation.models import MessageBlockType, TextBlock, ThinkingBlock, TurnRole
from conversation.store import SqlAlchemyConversationStore, ThreadNotFoundError
from db.models import ThreadRow


async def test_create_thread_then_get_round_trips(db_session: AsyncSession) -> None:
    store = SqlAlchemyConversationStore(db_session)
    thread = await store.create_thread(user_id="user-1", title="2330 chip flows")

    fetched = await store.get_thread(user_id="user-1", thread_id=thread.id)

    assert fetched.id == thread.id
    assert fetched.title == "2330 chip flows"
    assert fetched.turns == ()


async def test_get_thread_scoped_to_owning_user_raises(db_session: AsyncSession) -> None:
    store = SqlAlchemyConversationStore(db_session)
    thread = await store.create_thread(user_id="user-1", title="t")

    with pytest.raises(ThreadNotFoundError):
        await store.get_thread(user_id="user-2", thread_id=thread.id)


async def test_list_threads_excludes_archived_by_default(db_session: AsyncSession) -> None:
    store = SqlAlchemyConversationStore(db_session)
    await store.create_thread(user_id="user-1", title="a")
    archived = await store.create_thread(user_id="user-1", title="b")
    result = await db_session.execute(select(ThreadRow).where(ThreadRow.id == archived.id))
    result.scalar_one().archived = True
    await db_session.flush()

    listed = await store.list_threads(user_id="user-1")
    listed_with_archived = await store.list_threads(user_id="user-1", include_archived=True)

    assert len(listed) == 1
    assert len(listed_with_archived) == 2


async def test_append_turn_builds_a_message_chain_and_updates_active_leaf(
    db_session: AsyncSession,
) -> None:
    store = SqlAlchemyConversationStore(db_session)
    thread = await store.create_thread(user_id="user-1")

    turn = await store.append_turn(
        user_id="user-1",
        thread_id=thread.id,
        role=TurnRole.ASSISTANT,
        blocks=[(MessageBlockType.TEXT, TextBlock(text="hello"))],
        parent_message_id=None,
    )

    assert len(turn.messages) == 1
    fetched = await store.get_thread(user_id="user-1", thread_id=thread.id)
    assert fetched.active_leaf_message_id == turn.messages[0].id


async def test_active_path_walks_only_the_active_branch(db_session: AsyncSession) -> None:
    """A→B, then B→C1 and B→C2 (two children of the same parent): `active_path` must return
    exactly one root-to-leaf chain, never both branches."""
    store = SqlAlchemyConversationStore(db_session)
    thread = await store.create_thread(user_id="user-1")

    turn_a = await store.append_turn(
        user_id="user-1",
        thread_id=thread.id,
        role=TurnRole.ASSISTANT,
        blocks=[(MessageBlockType.TEXT, TextBlock(text="A"))],
        parent_message_id=None,
    )
    message_a = turn_a.messages[0]

    turn_c1 = await store.append_turn(
        user_id="user-1",
        thread_id=thread.id,
        role=TurnRole.ASSISTANT,
        blocks=[(MessageBlockType.TEXT, TextBlock(text="C1"))],
        parent_message_id=message_a.id,
    )
    await store.append_turn(
        user_id="user-1",
        thread_id=thread.id,
        role=TurnRole.ASSISTANT,
        blocks=[(MessageBlockType.TEXT, TextBlock(text="C2"))],
        parent_message_id=message_a.id,
    )

    # The active leaf is now C2 (the most recently appended turn); switch back to C1's leaf.
    await store.switch_branch(thread_id=thread.id, target_message_id=turn_c1.messages[0].id)
    path = await store.active_path(thread_id=thread.id)

    texts = [block.content.text for block in path if isinstance(block.content, TextBlock)]
    assert texts == ["A", "C1"]
    assert "C2" not in texts


async def test_switch_branch_is_an_o1_pointer_flip_not_a_copy(db_session: AsyncSession) -> None:
    store = SqlAlchemyConversationStore(db_session)
    thread = await store.create_thread(user_id="user-1")
    turn_a = await store.append_turn(
        user_id="user-1",
        thread_id=thread.id,
        role=TurnRole.ASSISTANT,
        blocks=[(MessageBlockType.TEXT, TextBlock(text="A"))],
        parent_message_id=None,
    )

    await store.switch_branch(thread_id=thread.id, target_message_id=turn_a.messages[0].id)

    fetched = await store.get_thread(user_id="user-1", thread_id=thread.id)
    assert fetched.active_leaf_message_id == turn_a.messages[0].id
    # No new message rows were created by switch_branch itself.
    assert sum(len(t.messages) for t in fetched.turns) == 1


async def test_fork_thread_creates_a_separate_thread_sharing_no_messages(
    db_session: AsyncSession,
) -> None:
    store = SqlAlchemyConversationStore(db_session)
    source = await store.create_thread(user_id="user-1", title="source")
    source_turn = await store.append_turn(
        user_id="user-1",
        thread_id=source.id,
        role=TurnRole.ASSISTANT,
        blocks=[(MessageBlockType.TEXT, TextBlock(text="A"))],
        parent_message_id=None,
    )

    forked = await store.fork_thread(
        user_id="user-1", source_thread_id=source.id, from_turn_id=source_turn.id
    )

    assert forked.id != source.id
    assert forked.turns == ()


async def test_delete_thread_soft_deletes(db_session: AsyncSession) -> None:
    store = SqlAlchemyConversationStore(db_session)
    thread = await store.create_thread(user_id="user-1")

    await store.delete_thread(user_id="user-1", thread_id=thread.id)

    listed = await store.list_threads(user_id="user-1")
    assert listed == []


async def test_search_matches_visible_text_but_not_thinking_or_other_users(
    db_session: AsyncSession,
) -> None:
    store = SqlAlchemyConversationStore(db_session)
    said = await store.create_thread(user_id="user-1", title="a")
    thought = await store.create_thread(user_id="user-1", title="b")
    other = await store.create_thread(user_id="user-2", title="c")
    for thread, user, block_type, block in (
        (said, "user-1", MessageBlockType.TEXT, TextBlock(text="台積電 法說會重點")),
        (thought, "user-1", MessageBlockType.THINKING, ThinkingBlock(text="台積電 internal")),
        (other, "user-2", MessageBlockType.TEXT, TextBlock(text="台積電 mine")),
    ):
        await store.append_turn(
            user_id=user,
            thread_id=thread.id,
            role=TurnRole.ASSISTANT,
            blocks=[(block_type, block)],
            parent_message_id=None,
        )

    found = await store.search_threads(user_id="user-1", query="台積電")

    assert [t.id for t in found] == [said.id]
