"""The shared streaming turn-driver: the one seam `POST /agent/query`, `POST
/threads/{id}/turns/{tid}/regenerate`, and `POST /threads/{id}/turns/{tid}/edit` all invoke to run
the orchestrator and stream its frames — extracted from `agent.py` so a second route can drive a
turn without forking (and inevitably drifting from) the governance-checked path.

Governance is not re-implemented here, same as `agent.py`'s own docstring: the answer text comes
from `Orchestrator.run()`, whose lanes and synthesis each run `guardrails.check_output`, and whose
every tool call routes through `tools.base.dispatch_tool`'s sponsor gate via the `ToolContext` the
caller hands in. This module adds no tool path of its own and no bypass of the pre-turn rate/quota
gate — `regenerate`/`edit` call `enforce_pre_turn_gate` exactly as `/agent/query` does, so a retry
or an edit-resend cannot dodge the daily-chat cap or the per-minute rate limit either.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from collections.abc import AsyncIterator

import structlog
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.entitlements import (
    chat_limit_for,
    feature_allowed,
    rate_limit_per_minute,
)
from accounts.models import User
from api.deep_research_turn import run_deep_research_turn
from api.dependencies import AppState
from api.errors import ApiError
from api.pet_turn import pet_hint_for_user, reward_completed_chat
from api.sse import format_sse_event
from api.turn_registry import RunningTurn
from conversation.models import (
    Framework,
    MessageBlockContent,
    MessageBlockType,
    RichCardBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    TurnRole,
    WebSearchSourcesBlock,
)
from conversation.store import (
    ConversationStore,
    ThreadNotFoundError,
)
from conversation.title_generator import generate_title
from conversation.turn_lifecycle import SimpleCancelToken
from events.bus import EventBus
from events.kinds import EventKind
from events.usage import MemoryRef, TurnUsage
from llm.base import ImagePart, Message, MessageRole, ToolCall, ToolResultPart
from llm.registry import ByokOverride, byok_scope
from memory.models import Memory
from memory.recall import recall_with_ids
from memory.settings import memory_enabled
from memory.store import SqlAlchemyMemoryStore
from memory.watchlists import WatchlistStore
from orchestrator.models import OrchestratorResult
from orchestrator.orchestrator import PersonalityContext
from tools.base import ToolContext
from usage.llm_usage import write_model_usage
from usage.store import write_turn_usage

_RATE_WINDOW_SECONDS = 60

# A research run fans out into many searches, page fetches and model calls, and there is no daily
# report cap, so one account must not be able to start them in bulk. This is a safety rate, not a
# quota: ordinary use (a report every few minutes) never meets it, nothing in the UI mentions it,
# and a refused start simply asks the user to try again shortly.
_DEEP_RESEARCH_STARTS = 4
_DEEP_RESEARCH_WINDOW_SECONDS = 600

logger = structlog.get_logger(__name__)


async def enforce_pre_turn_gate(
    state: AppState,
    session: AsyncSession,
    user: User,
    *,
    deep_research: bool = False,
    byok: bool,
) -> None:
    """Reject a turn before it is created when the caller is over their per-minute request rate or
    would exceed their daily-chat allowance. Shared by every route that starts a real turn
    (`/agent/query`, `regenerate`, `edit`) — a retry/regenerate/edit can never dodge the cap.

    Rate limit: an atomic Redis `INCR`+TTL check-and-increment keyed on the caller and window (the
    shared multi-worker backend) or its single-process in-memory equivalent — a `429` with a
    `retry_after_seconds` hint. Daily-chat quota: a reserve-before-act atomic increment of the
    caller's durable UTC-day counter, so the "has headroom?" check and the claim are one operation
    (no TOCTOU) — over-cap callers are refunded and rejected without spending the day's allowance.
    An unlimited tier (Pro) or an active BYOK key means no daily cap and no counter write.

    `deep_research=True` adds the Pro-only feature check (`403 feature_not_entitled`) and a short-
    window safety rate on report starts (`429 rate_limited`). A report has no daily cap: each run is
    bounded by its own budget, and the per-minute rate above and the daily-chat allowance still
    apply."""
    verdict = await state.rate_limiter.check(
        user.id,
        max_requests=rate_limit_per_minute(user),
        window_seconds=_RATE_WINDOW_SECONDS,
    )
    if not verdict.allowed:
        raise ApiError(
            status_code=429,
            code="rate_limited",
            message="Too many requests. Please slow down and retry shortly.",
            retryable=True,
        )

    if deep_research:
        _require_deep_research_feature(user)
        research_verdict = await state.rate_limiter.check(
            f"{user.id}:deep_research",
            max_requests=_DEEP_RESEARCH_STARTS,
            window_seconds=_DEEP_RESEARCH_WINDOW_SECONDS,
        )
        if not research_verdict.allowed:
            raise ApiError(
                status_code=429,
                code="rate_limited",
                message="Too many research requests. Please wait a few minutes and retry.",
                retryable=True,
            )

    # `byok` is the SAME decision the turn will run on (`api.byok.resolve_byok`), passed in rather
    # than looked up again here: the cap is lifted only for a turn that really runs on a user key,
    # so a key deleted between two lookups cannot leave a platform-key turn uncapped.
    limit = chat_limit_for(user, byok_active=byok)
    quota = await state.usage_store.reserve_daily_chat(user_id=user.id, limit=limit)
    if not quota.allowed:
        raise ApiError(
            status_code=429,
            code="chat_limit_reached",
            message=(
                f"Daily chat limit reached ({quota.limit} messages/day). "
                "Upgrade your plan or add your own API key to continue."
            ),
            retryable=False,
        )


def _require_deep_research_feature(user: User) -> None:
    if not feature_allowed(user, "deep_research"):
        raise ApiError(
            status_code=403,
            code="feature_not_entitled",
            message="Deep research is available on the Pro plan.",
            retryable=False,
        )


async def run_and_stream(
    *,
    state: AppState,
    store: ConversationStore,
    session: AsyncSession,
    event_bus: EventBus,
    user_id: str,
    thread_id: str,
    turn_id: str,
    message: str,
    ctx: ToolContext,
    personality: PersonalityContext,
    answer_parent_message_id: str,
    regenerate_of: str | None = None,
    model_override: str | None = None,
    web_search_enabled: bool = False,
    locale: str = "zh-TW",
    images: tuple[ImagePart, ...] = (),
    deep_research: bool = False,
    byok: ByokOverride | None = None,
) -> AsyncIterator[str]:
    """Subscribe to the bus first, then drive the orchestrator in a background task so no frame
    published between `turn.start` and `turn.end` is missed. Yields SSE frames until `turn.end`.

    This MUST stay an `async def` generator (not a plain function that returns one): the body
    below — `event_bus.subscribe()`, then `asyncio.create_task(...)` — only executes on the first
    `__anext__()`, i.e. when the `StreamingResponse` actually starts consuming it. That deferral is
    what guarantees the subscriber is registered before the driver task gets its first chance to
    run (the `async for` line's first step suspends on `queue.get()`, handing control to the
    scheduler only *after* this subscriber is in the bus's subscriber map) — publish-before-
    subscribe would otherwise silently drop the turn's early frames (`turn.start`, and sometimes
    more), which is exactly what happened when this was briefly written as an eager function that
    created the task before the caller began iterating the response body.

    `answer_parent_message_id` is the message the assistant's answer attaches under — the normal
    `/agent/query` flow computes it as "whatever the active leaf is right now" (the user message
    just appended), but `regenerate`/`edit` must pin it explicitly (the original parent, so the
    new assistant turn lands as a *sibling*, not wherever the active leaf happens to be).
    `regenerate_of` (a source turn id) is stamped on the new assistant turn only for `regenerate`
    -- it is the provenance link a client uses to render prior/next version navigation."""
    subscription = event_bus.subscribe()

    async def _noop() -> None:
        # Every LLM call this turn makes (classifier, lanes, synthesis, title) uses the user's
        # own model and key when `byok` is set; the scope ends with the turn.
        with byok_scope(byok):
            await _drive_run()

    async def _drive_run() -> None:
        await _drive_turn(
            state=state,
            store=store,
            session=session,
            event_bus=event_bus,
            user_id=user_id,
            thread_id=thread_id,
            turn_id=turn_id,
            message=message,
            ctx=ctx,
            personality=personality,
            answer_parent_message_id=answer_parent_message_id,
            regenerate_of=regenerate_of,
            model_override=model_override,
            web_search_enabled=web_search_enabled,
            locale=locale,
            images=images,
            deep_research=deep_research,
            byok=byok,
        )

    runner = asyncio.create_task(_noop())
    running = state.turn_registry.get(turn_id)
    if running is not None:
        running.task = runner
        if state.turn_control.enabled:
            running.control_task = asyncio.create_task(_serve_control(state, running))
    try:
        async for event in subscription:
            yield format_sse_event(event)
            if event.kind == EventKind.TURN_END:
                break
    finally:
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner
        if running is not None and running.control_task is not None:
            running.control_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running.control_task
            with contextlib.suppress(Exception):
                await state.turn_control.withdraw(turn_id=turn_id, thread_id=thread_id)
        state.turn_registry.unregister(turn_id)
        # A Redis-backed bus runs a background mirror-writer; drain + stop it so the per-turn bus
        # leaves no dangling task. The in-process bus has no such handle (duck-typed, optional).
        closer = getattr(event_bus, "aclose", None)
        if closer is not None:
            with contextlib.suppress(Exception):
                await closer()


async def _serve_control(state: AppState, running: RunningTurn) -> None:
    """Announce this turn to the other workers and apply the cancel/steer commands they relay."""
    control = state.turn_control
    try:
        await control.announce(
            turn_id=running.turn_id, user_id=running.user_id, thread_id=running.thread_id
        )
        async for command in control.listen(running.turn_id, running.thread_id):
            running.apply(command)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("turn_control_failed", turn_id=running.turn_id)


def start_turn_stream(
    *,
    state: AppState,
    store: ConversationStore,
    session: AsyncSession,
    user_id: str,
    thread_id: str,
    turn_id: str,
    message: str,
    ctx: ToolContext,
    personality: PersonalityContext,
    answer_parent_message_id: str,
    regenerate_of: str | None = None,
    model_override: str | None = None,
    web_search_enabled: bool = False,
    locale: str = "zh-TW",
    images: tuple[ImagePart, ...] = (),
    deep_research: bool = False,
    byok: ByokOverride | None = None,
) -> StreamingResponse:
    """Register the turn + return the `StreamingResponse` — the one call every route that starts
    a real turn makes, so registration/streaming plumbing exists in exactly one place."""
    event_bus = state.event_bus_factory.new_bus(turn_id=turn_id, user_id=user_id)
    cancel_token = SimpleCancelToken()
    state.turn_registry.register(
        turn_id=turn_id,
        user_id=user_id,
        thread_id=thread_id,
        event_bus=event_bus,
        cancel_token=cancel_token,
    )
    stream = run_and_stream(
        state=state,
        store=store,
        session=session,
        event_bus=event_bus,
        user_id=user_id,
        thread_id=thread_id,
        turn_id=turn_id,
        message=message,
        ctx=ctx,
        personality=personality,
        answer_parent_message_id=answer_parent_message_id,
        regenerate_of=regenerate_of,
        model_override=model_override,
        web_search_enabled=web_search_enabled,
        locale=locale,
        images=images,
        deep_research=deep_research,
        byok=byok,
    )
    return StreamingResponse(stream, media_type="text/event-stream")


async def _load_history(
    store: ConversationStore, *, user_id: str, thread_id: str, turn_id: str
) -> tuple[Message, ...]:
    """Prior turns on this thread, converted to `llm.base.Message`s the orchestrator/classifier
    can read — without this, every turn starts from a blank slate and a follow-up like "what
    about its dividend history?" has no way to resolve "its" to whatever symbol an earlier turn
    discussed. Excludes `turn_id` itself: by the time `_drive_turn` runs, the current turn's own
    user message is already persisted (`agent.py`/`threads_actions.py` append it before starting
    the stream) — the caller passes that text separately as `message`, so including it here would
    duplicate it. Only `TEXT` blocks are converted (tool/thinking/rich-card blocks are noise for
    conversational continuity, not signal) — a `Turn.role` of `assistant`/`user` maps directly to
    `MessageRole.ASSISTANT`/`USER`. Best-effort: a brand-new thread (no turns yet) or a lookup
    failure returns no history rather than failing the turn over conversational context."""
    try:
        thread = await store.get_thread(user_id=user_id, thread_id=thread_id)
    except ThreadNotFoundError:
        return ()
    history: list[Message] = []
    for turn in thread.turns:
        if turn.id == turn_id:
            continue
        role = MessageRole.USER if turn.role is TurnRole.USER else MessageRole.ASSISTANT
        for block in turn.messages:
            if block.type is MessageBlockType.TEXT:
                text = getattr(block.content, "text", None)
                if isinstance(text, str) and text:
                    history.append(Message(role=role, content=text))
    return tuple(history)


async def _drive_turn(
    *,
    state: AppState,
    store: ConversationStore,
    session: AsyncSession,
    event_bus: EventBus,
    user_id: str,
    thread_id: str,
    turn_id: str,
    message: str,
    ctx: ToolContext,
    personality: PersonalityContext,
    answer_parent_message_id: str,
    regenerate_of: str | None = None,
    model_override: str | None = None,
    web_search_enabled: bool = False,
    locale: str = "zh-TW",
    images: tuple[ImagePart, ...] = (),
    deep_research: bool = False,
    byok: ByokOverride | None = None,
) -> None:
    event_bus.publish(EventKind.TURN_START, turn_id=turn_id, data={"thread_id": thread_id})
    # Which 語氣 preset will shape this answer (turn > chat > account > default), shown under it.
    tone_id = state.personality_registry.resolve(
        turn_override=personality.turn_override,
        thread_override=personality.thread_override,
        user_preference=personality.user_preference,
    ).id
    event_bus.publish(EventKind.PERSONALITY_USED, turn_id=turn_id, data={"personality": tone_id})
    # The registry entry is `RunningTurn` itself — the same live handle `POST .../steer` appends
    # to and `.../cancel` flips — so the loop's `drain()` calls read exactly what a concurrent
    # steer request queued, with no second queue to keep in sync.
    running = state.turn_registry.get(turn_id)
    personality = dataclasses.replace(
        personality, pet_hint=await pet_hint_for_user(session, user_id=user_id)
    )
    history = await _load_history(store, user_id=user_id, thread_id=thread_id, turn_id=turn_id)

    # Long-term-memory recall block for this turn's root reply agent (injected as a `pre_llm`
    # SYSTEM message inside the orchestrator). Bound as a zero-arg thunk over this turn's
    # user/thread/query so the orchestrator — which has no `memory`/`conversation` layer access
    # per the dependency rule — never constructs the stores or knows the query shape itself.
    memory_store = SqlAlchemyMemoryStore(session)
    watchlist_store = WatchlistStore(session)

    memories_used: list[MemoryRef] = []

    async def recall_provider() -> str | None:
        if not await memory_enabled(session, user_id=user_id):
            return None  # the user paused memory: recall nothing
        block, used = await recall_with_ids(
            memory_store=memory_store,
            watchlist_store=watchlist_store,
            user_id=user_id,
            thread_id=thread_id,
            query=message,
        )
        if used:
            await memory_store.mark_used(user_id=user_id, memory_ids=[m.id for m in used])
            memories_used[:] = [_memory_ref(m) for m in used]
            event_bus.publish(
                EventKind.MEMORY_USED,
                turn_id=turn_id,
                data={"memories": [r.model_dump() for r in memories_used]},
            )
        return block

    try:
        if deep_research:
            result = await run_deep_research_turn(
                state=state,
                ctx=ctx,
                event_bus=event_bus,
                turn_id=turn_id,
                question=message,
                locale=locale,
                model_override=model_override,
                history=history,
                images=images,
            )
        else:
            result = await state.orchestrator.run(
                message,
                tool_context=ctx,
                event_bus=event_bus,
                turn_id=turn_id,
                personality=personality,
                steering_source=running,
                model_override=model_override,
                web_search_enabled=web_search_enabled,
                locale=locale,
                images=images,
                history=history,
                recall_provider=recall_provider,
            )
    except asyncio.CancelledError:
        # A cancel request (via the turn registry) hard-stops the driver — end the stream with a
        # cancelled frame rather than letting the cancellation propagate as a silent drop.
        event_bus.publish(EventKind.TURN_END, turn_id=turn_id, data={"status": "cancelled"})
        raise
    except Exception as exc:  # a failed turn ends the stream with an error frame, never a hang
        logger.error("turn_drive_failed", turn_id=turn_id, error=str(exc))
        event_bus.publish(
            EventKind.TURN_END, turn_id=turn_id, data={"status": "error", "message": str(exc)}
        )
        return

    result = _with_memories(result, turn_id=turn_id, memories=memories_used)
    result = _with_personality(result, turn_id=turn_id, preset_id=tone_id)
    await _persist_answer(
        store,
        user_id=user_id,
        thread_id=thread_id,
        result=result,
        parent_message_id=answer_parent_message_id,
        regenerate_of=regenerate_of,
    )
    await _maybe_title_thread(store, user_id=user_id, thread_id=thread_id, message=message)
    await _record_usage(
        session, user_id=user_id, turn_id=turn_id, result=result, byok=byok is not None
    )
    event_bus.publish(
        EventKind.MESSAGE,
        turn_id=turn_id,
        data={"text": result.final_text, "framework": result.routing.framework},
    )
    await reward_completed_chat(session, event_bus=event_bus, turn_id=turn_id, user_id=user_id)
    event_bus.publish(EventKind.TURN_END, turn_id=turn_id, data={"status": "done"})


async def _maybe_title_thread(
    store: ConversationStore, *, user_id: str, thread_id: str, message: str
) -> None:
    """Title a brand-new thread from its first user message, once, right after the first answer
    lands — an empty `title` (the value `create_thread` defaults to) is the cheap, already-durable
    signal that this is the thread's first turn, so no turn-count query is needed. Best-effort
    exactly like `_record_usage` below: a failure here must never fail an already-answered turn,
    so read-current-title, generate, and persist are each guarded and swallowed on error."""
    try:
        thread = await store.get_thread(user_id=user_id, thread_id=thread_id)
    except ThreadNotFoundError:
        return
    if thread.title:
        return
    try:
        title = await generate_title(message)
        await store.update_thread(user_id=user_id, thread_id=thread_id, title=title)
    except Exception as exc:  # cosmetic — never fail an already-answered turn over a title
        logger.warning("thread_titling_failed", thread_id=thread_id, error=repr(exc))


_MEMORY_SNIPPET_CHARS = 80


def _memory_ref(memory: Memory) -> MemoryRef:
    raw = memory.content.get("text")
    text = raw if isinstance(raw, str) else " ".join(str(v) for v in memory.content.values())
    flat = " ".join(text.split())
    snippet = flat if len(flat) <= _MEMORY_SNIPPET_CHARS else f"{flat[:_MEMORY_SNIPPET_CHARS]}…"
    return MemoryRef(id=memory.id, key=memory.key, type=memory.type.value, text=snippet)


def _with_memories(
    result: OrchestratorResult, *, turn_id: str, memories: list[MemoryRef]
) -> OrchestratorResult:
    """The result with the memories the turn used recorded on its usage snapshot, so a reopened
    thread can still show which memories informed an answer."""
    if not memories:
        return result
    usage = result.usage or TurnUsage(turn_id=turn_id)
    return result.model_copy(
        update={"usage": usage.model_copy(update={"memories_used": tuple(memories)})}
    )


def _with_personality(
    result: OrchestratorResult, *, turn_id: str, preset_id: str
) -> OrchestratorResult:
    """The result with the 語氣 preset recorded on its usage snapshot."""
    usage = result.usage or TurnUsage(turn_id=turn_id)
    return result.model_copy(update={"usage": usage.model_copy(update={"personality": preset_id})})


async def _record_usage(
    session: AsyncSession, *, user_id: str, turn_id: str, result: OrchestratorResult, byok: bool
) -> None:
    """Write the finished turn's usage rollup through to the durable sink on the request's own
    session (one connection, no second writer contending). Idempotent on `(user_id, turn_id)`, so
    a retry cannot double-count. Best-effort relative to the turn: a write failure is rolled back
    and logged, never allowed to fail the already-answered turn."""
    usage = result.usage
    if usage is None:
        return
    try:
        await write_turn_usage(
            session,
            user_id=user_id,
            turn_id=turn_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            tool_calls=usage.tool_calls,
            finmind_calls=usage.finmind_calls,
            cost_micros=usage.cost_micros,
        )
        await write_model_usage(
            session, user_id=user_id, turn_id=turn_id, models=usage.models, byok=byok
        )
        await session.commit()
    except Exception as exc:  # a durable-usage write is best-effort — never fail an answered turn
        await session.rollback()
        logger.warning("usage_record_failed", turn_id=turn_id, error=repr(exc))


def _lane_process_blocks(
    result: OrchestratorResult,
) -> list[tuple[MessageBlockType, MessageBlockContent]]:
    """Every lane's tool-call/thinking trace, as durable blocks — the persisted analogue of the
    live SSE `tool.result`/`thinking.delta` frames, so a reopened thread's process disclosure
    isn't limited to the live stream (see `agent.loop.TurnResult.tool_calls`/`thinking_text`'s own
    docstrings for where this data comes from). Each block's `agent_id` lets the frontend group
    them by lane the same way `turn-store.ts`'s `buildLaneGroups` already does for the live view.
    A tool call's arguments are never persisted (only its name + already-redacted-summary result,
    same "provenance not payload" rule the live journal follows) — `ToolUseBlock.call.arguments`
    is always empty here, `activity` reuses the tool name since no richer NL label survives past
    the live stream."""
    blocks: list[tuple[MessageBlockType, MessageBlockContent]] = []
    for lane in result.lanes:
        for index, call in enumerate(lane.tool_calls):
            call_id = f"{lane.agent_id}:{call.step_index}:{index}"
            blocks.append(
                (
                    MessageBlockType.TOOL_USE,
                    ToolUseBlock(
                        call=ToolCall(id=call_id, name=call.tool_name, arguments={}),
                        activity=call.tool_name,
                        agent_id=lane.agent_id,
                    ),
                )
            )
            blocks.append(
                (
                    MessageBlockType.TOOL_RESULT,
                    ToolResultBlock(
                        result=ToolResultPart(tool_call_id=call_id, content=call.summary),
                        agent_id=lane.agent_id,
                    ),
                )
            )
        if lane.thinking_text:
            blocks.append(
                (
                    MessageBlockType.THINKING,
                    ThinkingBlock(text=lane.thinking_text, agent_id=lane.agent_id),
                )
            )
    return blocks


async def _persist_answer(
    store: ConversationStore,
    *,
    user_id: str,
    thread_id: str,
    result: OrchestratorResult,
    parent_message_id: str,
    regenerate_of: str | None = None,
) -> None:
    blocks: list[tuple[MessageBlockType, MessageBlockContent]] = [*_lane_process_blocks(result)]
    # Persist each governed card as its own block, BEFORE the answer text — matches the live
    # stream's own order (`active-turn-view.tsx` renders `turn.richCards` above `turn.answerText`,
    # since a tool's `rich_card` event fires mid-turn, before the final synthesis text). Ordering
    # this any other way means a turn renders cards-then-answer live but flips to answer-then-cards
    # the moment the thread reloads into `PersistedThreadView` — the exact "cards ended up below
    # the caveat" regression this fixes.
    blocks.extend(
        (MessageBlockType.RICH_CARD, RichCardBlock(card_type=card.card_type, data=card.data))
        for card in result.rich_cards
    )
    blocks.append((MessageBlockType.TEXT, TextBlock(text=result.final_text)))
    # One block for every source this turn's native web search consulted, so a re-opened thread
    # still shows the "N sources" pill `web-search-sources.tsx` renders — without this, `web_
    # search.result` was a live-only SSE event with nowhere to land once the turn finished (see
    # `agent.loop.TurnResult.web_search_sources`'s own docstring for where these are collected).
    if result.web_search_sources:
        blocks.append(
            (
                MessageBlockType.WEB_SEARCH_SOURCES,
                WebSearchSourcesBlock(sources=result.web_search_sources),
            )
        )
    await store.append_turn(
        user_id=user_id,
        thread_id=thread_id,
        role=TurnRole.ASSISTANT,
        blocks=blocks,
        parent_message_id=parent_message_id,
        framework=Framework(result.routing.framework),
        regenerate_of=regenerate_of,
        usage=result.usage,
    )


async def current_leaf(store: ConversationStore, thread_id: str) -> str | None:
    path = await store.active_path(thread_id=thread_id)
    return path[-1].id if path else None


__all__ = [
    "current_leaf",
    "enforce_pre_turn_gate",
    "run_and_stream",
    "start_turn_stream",
]
