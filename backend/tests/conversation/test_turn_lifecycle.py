"""`TurnLifecycle` — the accepted->running->(done|cancelled|error) state machine,
idempotency-key handling, and its real integration with `agent.loop.AgentLoop.run_turn`."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from agent.loop import AgentLoop
from conversation.store import SqlAlchemyConversationStore
from conversation.turn_lifecycle import SimpleCancelToken, TurnLifecycle, TurnLifecycleState
from events.bus import InMemoryEventBus
from tests.llm.stubs import StubLLMClient, make_text_response
from tools.base import ToolContext


async def test_start_runs_a_turn_to_done_and_persists_it(db_session: AsyncSession) -> None:
    conversation_store = SqlAlchemyConversationStore(db_session)
    thread = await conversation_store.create_thread(user_id="user-1")
    bus = InMemoryEventBus()
    lifecycle = TurnLifecycle(conversation_store=conversation_store, event_bus=bus)
    llm = StubLLMClient(script=[make_text_response("here is the answer")])
    loop = AgentLoop(llm, tools=[], max_steps=2)

    handle = await lifecycle.start(
        loop=loop,
        user_id="user-1",
        thread_id=thread.id,
        user_message="hi",
        tool_context=ToolContext(),
    )

    assert lifecycle.state_of(handle.turn_id) is TurnLifecycleState.DONE
    fetched = await conversation_store.get_thread(user_id="user-1", thread_id=thread.id)
    assert len(fetched.turns) == 1


async def test_start_publishes_turn_start_and_turn_end_events(db_session: AsyncSession) -> None:
    conversation_store = SqlAlchemyConversationStore(db_session)
    thread = await conversation_store.create_thread(user_id="user-1")
    bus = InMemoryEventBus()
    lifecycle = TurnLifecycle(conversation_store=conversation_store, event_bus=bus)
    llm = StubLLMClient(script=[make_text_response("ok")])
    loop = AgentLoop(llm, tools=[], max_steps=2)

    kinds: list[str] = []

    async def collect() -> None:
        async for event in bus.subscribe(["turn.start", "turn.end"]):
            kinds.append(event.kind)
            if len(kinds) == 2:
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await lifecycle.start(
        loop=loop,
        user_id="user-1",
        thread_id=thread.id,
        user_message="hi",
        tool_context=ToolContext(),
    )
    await task

    assert kinds == ["turn.start", "turn.end"]


async def test_repeated_idempotency_key_does_not_spawn_a_second_turn(
    db_session: AsyncSession,
) -> None:
    conversation_store = SqlAlchemyConversationStore(db_session)
    thread = await conversation_store.create_thread(user_id="user-1")
    bus = InMemoryEventBus()
    lifecycle = TurnLifecycle(conversation_store=conversation_store, event_bus=bus)
    llm = StubLLMClient(script=[make_text_response("first answer")])
    loop = AgentLoop(llm, tools=[], max_steps=2)

    first = await lifecycle.start(
        loop=loop,
        user_id="user-1",
        thread_id=thread.id,
        user_message="hi",
        tool_context=ToolContext(),
        idempotency_key="key-1",
    )
    second = await lifecycle.start(
        loop=loop,
        user_id="user-1",
        thread_id=thread.id,
        user_message="hi again",
        tool_context=ToolContext(),
        idempotency_key="key-1",
    )

    assert first.turn_id == second.turn_id
    # Only one LLM call happened — the second `start()` never re-ran the loop.
    assert len(llm.calls) == 1


async def test_pre_cancelled_token_yields_a_cancelled_turn_recorded_in_the_store(
    db_session: AsyncSession,
) -> None:
    conversation_store = SqlAlchemyConversationStore(db_session)
    thread = await conversation_store.create_thread(user_id="user-1")
    bus = InMemoryEventBus()
    lifecycle = TurnLifecycle(conversation_store=conversation_store, event_bus=bus)
    llm = StubLLMClient(script=[])  # never consulted — cancellation fires before any chat() call
    loop = AgentLoop(llm, tools=[], max_steps=2)

    already_cancelled = SimpleCancelToken()
    already_cancelled.cancel()

    handle = await lifecycle.start(
        loop=loop,
        user_id="user-1",
        thread_id=thread.id,
        user_message="hi",
        tool_context=ToolContext(),
        cancel_token=already_cancelled,
    )

    assert lifecycle.state_of(handle.turn_id) is TurnLifecycleState.CANCELLED
    fetched = await conversation_store.get_thread(user_id="user-1", thread_id=thread.id)
    assert len(fetched.turns) == 1
    assert llm.calls == []
