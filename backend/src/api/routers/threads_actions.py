"""Thread management + turn-lifecycle actions layered on the already-built conversation store,
turn registry, and event bus.

Every route is user-scoped: the store's methods take `user_id` and filter on it, so a caller can
never read, mutate, cancel, or steer another user's thread/turn (per-user isolation). The
turn-lifecycle actions (attach-stream, steer, cancel) act on a *live* turn via the in-process
`TurnRegistry` that `/agent/query` populates.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import User
from api.dependencies import AppState, get_session, get_state, require_user, tool_context_for
from api.errors import ApiError
from api.sse import format_sse_event
from api.turn_driver import enforce_pre_turn_gate, start_turn_stream
from api.turn_registry import RunningTurn
from conversation.models import MessageBlockType, TextBlock, Thread, Turn, TurnRole
from conversation.store import (
    ConversationStore,
    MessageNotFoundError,
    SqlAlchemyConversationStore,
    StaleThreadVersionError,
    ThreadNotFoundError,
    TurnNotFoundError,
)
from events.bus import TURN_END_KIND, Event
from events.factory import StreamReader
from feedback.store import FeedbackRecord, FeedbackStore, SqlAlchemyFeedbackStore
from orchestrator.orchestrator import PersonalityContext

router = APIRouter(tags=["threads"])

_SSE_MEDIA_TYPE = "text/event-stream"
_NO_SOURCE_USER_TURN_MESSAGE = "This assistant turn has no source user turn to re-run."


def _store(session: AsyncSession = Depends(get_session)) -> ConversationStore:
    return SqlAlchemyConversationStore(session)


def _thread_not_found(thread_id: str) -> ApiError:
    return ApiError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="thread_not_found",
        message=f"Thread {thread_id} not found.",
        retryable=False,
    )


async def _require_owned_thread(
    store: ConversationStore, *, user_id: str, thread_id: str
) -> Thread:
    """Load a thread scoped to the caller, or 404. The store filters on `user_id`, so another
    user's thread is indistinguishable from a missing one — no cross-user existence leak."""
    try:
        return await store.get_thread(user_id=user_id, thread_id=thread_id)
    except ThreadNotFoundError as exc:
        raise _thread_not_found(thread_id) from exc


# ---- thread management ---------------------------------------------------------------------


class PatchThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    starred: bool | None = None
    archived: bool | None = None
    tags: tuple[str, ...] | None = None


@router.patch("/threads/{thread_id}")
async def patch_thread(
    thread_id: str,
    body: PatchThreadRequest,
    user: User = Depends(require_user),
    store: ConversationStore = Depends(_store),
    if_match: int | None = Header(default=None, alias="If-Match"),
) -> Thread:
    """Rename / star / archive / tag. `If-Match: <version>` enforces optimistic concurrency —
    a stale version means another device edited first, surfaced as 409."""
    try:
        return await store.update_thread(
            user_id=user.id,
            thread_id=thread_id,
            title=body.title,
            starred=body.starred,
            archived=body.archived,
            tags=body.tags,
            expected_version=if_match,
        )
    except ThreadNotFoundError as exc:
        raise _thread_not_found(thread_id) from exc
    except StaleThreadVersionError as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="stale_version",
            message="Thread was modified by another device — refetch and retry.",
            retryable=True,
        ) from exc


class ForkThreadRequest(BaseModel):
    from_turn_id: str = Field(min_length=1)
    title: str = ""


@router.post("/threads/{thread_id}/fork", status_code=status.HTTP_201_CREATED)
async def fork_thread(
    thread_id: str,
    body: ForkThreadRequest,
    user: User = Depends(require_user),
    store: ConversationStore = Depends(_store),
) -> Thread:
    """Fork from a turn into a fresh thread (shares no message rows with the source)."""
    try:
        return await store.fork_thread(
            user_id=user.id,
            source_thread_id=thread_id,
            from_turn_id=body.from_turn_id,
            title=body.title,
        )
    except ThreadNotFoundError as exc:
        raise _thread_not_found(thread_id) from exc


@router.get("/threads/search")
async def search_threads(
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(require_user),
    store: ConversationStore = Depends(_store),
) -> dict[str, list[Thread]]:
    """Full-text-ish search over the caller's own thread titles + message text."""
    return {"threads": await store.search_threads(user_id=user.id, query=q, limit=limit)}


@router.get("/threads/{thread_id}/export", response_model=None)
async def export_thread(
    thread_id: str,
    export_format: str = Query("json", alias="format", pattern="^(md|json)$"),
    user: User = Depends(require_user),
    store: ConversationStore = Depends(_store),
) -> StreamingResponse | PlainTextResponse:
    """Export a thread + its history as JSON or Markdown."""
    thread = await _require_owned_thread(store, user_id=user.id, thread_id=thread_id)
    if export_format == "json":
        body = thread.model_dump_json(indent=2)
        return StreamingResponse(iter([body]), media_type="application/json")
    return PlainTextResponse(_thread_to_markdown(thread), media_type="text/markdown")


def _thread_to_markdown(thread: Thread) -> str:
    lines = [f"# {thread.title or thread.id}", ""]
    for turn in thread.turns:
        lines.append(f"## {turn.role.value}")
        for message in turn.messages:
            text = getattr(message.content, "text", None)
            if text:
                lines.append(text)
        lines.append("")
    return "\n".join(lines)


# ---- turn-lifecycle actions ----------------------------------------------------------------


async def _attach_local_stream(running: RunningTurn, after_seq: int) -> AsyncIterator[str]:
    """Same-worker live attach — the in-memory backend has no durable/cross-worker log to
    replay from, so this is the only path there. Pulled out of `stream_thread` purely to keep
    that function's own branching shallow."""
    async for event in running.event_bus.subscribe():
        if event.seq <= after_seq:
            continue  # already delivered before the drop — never re-send (no duplicate)
        yield format_sse_event(event)
        if event.kind == TURN_END_KIND:
            break


def _same_worker_stream_response(
    running: RunningTurn | None, *, user_id: str, after_seq: int
) -> StreamingResponse:
    if running is None or running.user_id != user_id:
        return StreamingResponse(iter([]), media_type=_SSE_MEDIA_TYPE)
    return StreamingResponse(_attach_local_stream(running, after_seq), media_type=_SSE_MEDIA_TYPE)


def _resolve_durable_turn_id(thread: Thread, running: RunningTurn | None) -> str | None:
    """The turn this thread's cross-worker stream belongs to: the live registry if this worker
    happens to hold it, else the thread's latest turn from the store (a reconnect can land on a
    worker that never ran the turn)."""
    if running is not None:
        return running.turn_id
    return thread.turns[-1].id if thread.turns else None


@router.get("/threads/{thread_id}/stream")
async def stream_thread(
    thread_id: str,
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: ConversationStore = Depends(_store),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Attach to a thread's running turn and stream its remaining frames, with durable
    `Last-Event-ID` resume when the transport backend supports it.

    Two paths, chosen by the configured transport:

    - **Same worker (or single-process `memory` backend):** if this worker holds the live turn in
      its registry, attach to the in-process bus directly. Live-only — the in-memory backend has
      no durable/cross-worker log to replay from.
    - **Cross worker (`redis` backend):** subscribe to the turn's live Redis channel first, then
      replay everything the client missed from the durable log, then emit live frames — filtering
      out any whose `seq` the replay already covered. A reconnect after the turn ended still
      replays the tail from the durable log.

    A reconnect with nothing to attach to and no durable reader returns an immediately-closing
    stream rather than an error.
    """
    thread = await _require_owned_thread(store, user_id=user.id, thread_id=thread_id)
    after_seq = _parse_last_event_id(last_event_id)

    running = state.turn_registry.active_for_thread(thread_id)
    reader = state.event_bus_factory.reader()

    if reader is None:
        return _same_worker_stream_response(running, user_id=user.id, after_seq=after_seq)

    turn_id = _resolve_durable_turn_id(thread, running)
    if turn_id is None:
        return StreamingResponse(iter([]), media_type=_SSE_MEDIA_TYPE)

    return StreamingResponse(
        _resume_stream(reader, turn_id=turn_id, after_seq=after_seq),
        media_type=_SSE_MEDIA_TYPE,
    )


def _parse_last_event_id(last_event_id: str | None) -> int:
    """`Last-Event-ID` is the `seq` of the last frame the client received. Absent/malformed means
    "from the beginning" → -1, so the first real event (`seq=0`) is strictly greater and is
    delivered. A well-formed value N means the client has 0..N, so replay/live emit only seq > N."""
    if last_event_id is None:
        return -1
    try:
        return int(last_event_id)
    except ValueError:
        return -1


async def _resume_stream(
    reader: StreamReader, *, turn_id: str, after_seq: int
) -> AsyncIterator[str]:
    """Subscribe live FIRST, then replay the durable log, then follow live — the ordering that
    makes resume gapless and duplicate-free.

    Boundary walk-through. The client last saw `seq = after_seq` (or -1 if fresh). We want exactly
    the events with `seq > after_seq`, each once, in order.

    1. Open the live subscription and start buffering it into a queue **before** reading the log,
       so any event published during replay is captured live rather than falling into the gap
       between "log read completes" and "live attach happens".
    2. Replay `seq > after_seq` from the durable log and emit them. Strict `>` means the client
       never re-receives `after_seq` itself — no duplicate at the lower boundary.
    3. Track the highest seq emitted (`last_emitted`), then drain the live queue emitting only
       `seq > last_emitted` — an event that was both persisted (already replayed) and captured
       live is dropped, so the replay/live overlap produces no duplicate at the upper boundary.
       Anything newer than the log flows straight through.
    """
    queue: asyncio.Queue[Event] = asyncio.Queue()

    async def _buffer_live() -> None:
        async for event in reader.live(turn_id):
            queue.put_nowait(event)

    buffer_task = asyncio.create_task(_buffer_live())
    try:
        last_emitted = after_seq
        for event in await reader.replay_since(turn_id, after_seq):
            yield format_sse_event(event)
            last_emitted = max(last_emitted, event.seq)
            if event.kind == TURN_END_KIND:
                return
        # Follow live from the queue, de-duplicating against what replay already covered.
        while True:
            event = await queue.get()
            if event.seq <= last_emitted:
                continue
            yield format_sse_event(event)
            last_emitted = max(last_emitted, event.seq)
            if event.kind == TURN_END_KIND:
                return
    finally:
        buffer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await buffer_task


class SteerRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.post("/threads/{thread_id}/steer")
async def steer_turn(
    thread_id: str,
    body: SteerRequest,
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: ConversationStore = Depends(_store),
) -> dict[str, str]:
    """Queue a steering message into the running turn (redirect, no cancel).

    The steering text is recorded on the live turn handle (`RunningTurn.steering`) and a
    `steering.accepted` frame is published to the turn's stream so the client reflects it. The
    running `AgentLoop` (for the `single`-framework interactive root lane — see
    `Orchestrator.run`'s `steering_source` docstring for the fan-out-lane scope note) polls that
    same handle at its next Step boundary (`agent.loop.AgentLoop._inject_steering`), folds the
    message in as a new user-role turn, and clears it — never redirecting mid-Step, only between
    Steps, so an in-flight tool dispatch always finishes first.
    """
    await _require_owned_thread(store, user_id=user.id, thread_id=thread_id)
    running = state.turn_registry.active_for_thread(thread_id)
    if running is None or running.user_id != user.id:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="no_running_turn",
            message="No running turn to steer on this thread.",
            retryable=False,
        )
    running.steering.append(body.message)
    running.event_bus.publish(
        "steering.accepted", turn_id=running.turn_id, data={"message": body.message}
    )
    return {"status": "accepted"}


class SwitchBranchRequest(BaseModel):
    target_message_id: str = Field(min_length=1)


@router.post("/threads/{thread_id}/switch-branch")
async def switch_branch(
    thread_id: str,
    body: SwitchBranchRequest,
    user: User = Depends(require_user),
    store: ConversationStore = Depends(_store),
) -> Thread:
    """Switch the active path to another message (an O(1) leaf-pointer flip).

    The Branch Summary of the branch being left is not generated here: `summarize_branch` needs a
    cheap-role `LLMClient` handle, which the interface layer does not hold (the orchestrator owns
    the model clients and exposes no summary seam to a router). The pointer flip is real and
    complete; automatic branch-summary generation is a follow-up once that seam is exposed.
    """
    await _require_owned_thread(store, user_id=user.id, thread_id=thread_id)
    await store.switch_branch(thread_id=thread_id, target_message_id=body.target_message_id)
    return await _require_owned_thread(store, user_id=user.id, thread_id=thread_id)


@router.post("/threads/{thread_id}/turns/{turn_id}/cancel")
async def cancel_turn(
    thread_id: str,
    turn_id: str,
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: ConversationStore = Depends(_store),
) -> dict[str, str]:
    """Cancel a running turn (stops its SSE with a cancelled frame). Idempotent-ish: cancelling a
    turn that already finished/never existed is a 404, not a hang."""
    await _require_owned_thread(store, user_id=user.id, thread_id=thread_id)
    running = state.turn_registry.get(turn_id)
    if running is None or running.user_id != user.id or running.thread_id != thread_id:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="turn_not_found",
            message=f"No running turn {turn_id} on this thread.",
            retryable=False,
        )
    running.request_cancel()
    return {"status": "cancelling"}


def _feedback_store(session: AsyncSession = Depends(get_session)) -> FeedbackStore:
    return SqlAlchemyFeedbackStore(session)


class TurnFeedbackRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rating: str = Field(pattern="^(up|down)$")
    comment: str | None = Field(default=None, max_length=500)


@router.post("/threads/{thread_id}/turns/{turn_id}/feedback")
async def turn_feedback(
    thread_id: str,
    turn_id: str,
    body: TurnFeedbackRequest,
    user: User = Depends(require_user),
    store: ConversationStore = Depends(_store),
    feedback_store: FeedbackStore = Depends(_feedback_store),
) -> FeedbackRecord:
    """Record 👍/👎 + an optional short comment on a turn — a real, persisted write.

    Deliberately minimal: this is a rating capture, not the quality-score/alert pipeline the
    stale spec once described (rolling 7-day score, alert thresholds, admin `quality`/`alerts`/
    `recent` endpoints) — that remains unbuilt and is a dedicated follow-up. `submit` upserts on
    `(turn_id, user_id)`, so a re-submit (thumbs-down then thumbs-up) updates the one row rather
    than accumulating duplicates.
    """
    await _load_turn(store, user_id=user.id, thread_id=thread_id, turn_id=turn_id)
    return await feedback_store.submit(
        turn_id=turn_id, user_id=user.id, rating=body.rating, comment=body.comment
    )


async def _load_turn(
    store: ConversationStore, *, user_id: str, thread_id: str, turn_id: str
) -> Turn:
    try:
        return await store.get_turn(user_id=user_id, thread_id=thread_id, turn_id=turn_id)
    except ThreadNotFoundError as exc:
        raise _thread_not_found(thread_id) from exc
    except TurnNotFoundError as exc:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="turn_not_found",
            message=f"Turn {turn_id} not found.",
            retryable=False,
        ) from exc


def _turn_user_text(turn: Turn) -> str:
    """The plain text of a user turn's sole/first `text` block — what `regenerate`/`edit` feed
    back into the turn-driver as the (original or edited) user input."""
    for message in turn.messages:
        if isinstance(message.content, TextBlock):
            return message.content.text
    raise ApiError(
        status_code=status.HTTP_409_CONFLICT,
        code="turn_has_no_text",
        message="Turn has no text content to regenerate/edit from.",
        retryable=False,
    )


def _require_user_turn(turn: Turn) -> None:
    if turn.role is not TurnRole.USER:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="not_a_user_turn",
            message="Only a user turn can be regenerated/edited from.",
            retryable=False,
        )


def _first_text_message_parent_id(turn: Turn) -> str | None:
    """The message id the turn's own (first) text block hangs under — the branch point a new
    sibling turn attaches to, resolved from the turn's own root message rather than the thread's
    current active leaf (which `switch_branch` may have since moved elsewhere)."""
    for message in turn.messages:
        if isinstance(message.content, TextBlock):
            return message.parent_id
    return None


async def _source_user_turn(
    store: ConversationStore, *, user_id: str, thread_id: str, assistant_turn: Turn
) -> Turn:
    """Walk from an assistant turn back to the user turn that produced it. `parent_turn_id` is
    not populated by `append_turn` (nothing in this codebase writes it yet), so the real link is
    the message tree: the assistant turn's first message's `parent_id` is a message belonging to
    the user turn that preceded it — resolve that message's owning `turn_id` via `get_message`
    and load that turn."""
    parent_message_id = _first_text_message_parent_id(assistant_turn)
    if parent_message_id is None:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="no_source_user_turn",
            message=_NO_SOURCE_USER_TURN_MESSAGE,
            retryable=False,
        )
    try:
        parent_message = await store.get_message(user_id=user_id, message_id=parent_message_id)
    except MessageNotFoundError as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="no_source_user_turn",
            message=_NO_SOURCE_USER_TURN_MESSAGE,
            retryable=False,
        ) from exc
    return await _load_turn(
        store, user_id=user_id, thread_id=thread_id, turn_id=parent_message.turn_id
    )


@router.post("/threads/{thread_id}/turns/{turn_id}/regenerate")
async def regenerate_turn(
    thread_id: str,
    turn_id: str,
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: ConversationStore = Depends(_store),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Re-run the same user input that produced `turn_id`'s assistant answer, producing a NEW
    sibling assistant turn — a version, not an edit of history.

    Reuses the exact tree-branching primitive `switch_branch` relies on: `ConversationStore.
    append_turn`'s `parent_message_id` is what makes a new turn a *sibling* rather than a
    descendant — passing the source user turn's own last message id (the same parent the
    original assistant turn was appended under) makes the new assistant turn a sibling of the one
    being regenerated, both hanging off the same user message. No new store method was needed;
    `append_turn` already supports this shape. The turn-driver invocation is the same
    `api.turn_driver.start_turn_stream` seam `/agent/query` uses — same guardrail/entitlement/
    journal path, no bypass — seeded with the original turn's user text instead of a fresh
    message. `regenerate_of` on the new turn records which turn it re-ran.
    """
    turn = await _load_turn(store, user_id=user.id, thread_id=thread_id, turn_id=turn_id)
    if turn.role is not TurnRole.ASSISTANT:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="not_an_assistant_turn",
            message="Only an assistant turn can be regenerated.",
            retryable=False,
        )
    source_user_turn = await _source_user_turn(
        store, user_id=user.id, thread_id=thread_id, assistant_turn=turn
    )
    _require_user_turn(source_user_turn)
    message = _turn_user_text(source_user_turn)
    if not source_user_turn.messages:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="no_source_user_turn",
            message=_NO_SOURCE_USER_TURN_MESSAGE,
            retryable=False,
        )
    sibling_parent_id = source_user_turn.messages[-1].id

    thread = await _require_owned_thread(store, user_id=user.id, thread_id=thread_id)
    ctx = tool_context_for(user, state)
    await enforce_pre_turn_gate(state, session, user)

    new_turn_id = str(uuid.uuid4())
    personality = PersonalityContext(
        thread_override=thread.personality_override, user_preference=user.personality_id
    )
    return start_turn_stream(
        state=state,
        store=store,
        session=session,
        user_id=user.id,
        thread_id=thread_id,
        turn_id=new_turn_id,
        message=message,
        ctx=ctx,
        personality=personality,
        answer_parent_message_id=sibling_parent_id,
        regenerate_of=turn_id,
    )


class EditTurnRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str = Field(min_length=1, max_length=8000)


@router.post("/threads/{thread_id}/turns/{turn_id}/edit")
async def edit_turn(
    thread_id: str,
    turn_id: str,
    body: EditTurnRequest,
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: ConversationStore = Depends(_store),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Edit a user message + resend — creates a NEW branch: a new user turn as a sibling of the
    original (same parent message), with a new assistant turn as its child. The original user
    turn's row is never touched — canonical immutability (governance) means an edit must never
    rewrite an existing message row, only add a new sibling subtree.

    Reuses the same tree-branching shape as `regenerate`: the new user turn's `parent_message_id`
    is the ORIGINAL user turn's own parent (so it lands as a sibling, exactly what `switch_branch`
    would navigate between afterward), and the new assistant turn is then appended as that new
    user turn's child. Runs through the same `api.turn_driver.start_turn_stream` seam as
    `/agent/query` and `regenerate` — same guardrail/entitlement/journal path.

    Automatic branch-summary generation for the branch being left is not performed here, for the
    same reason `switch_branch` doesn't do it either: that needs a cheap-role `LLMClient` handle
    the interface layer does not hold today (see `switch_branch`'s own docstring). The branch
    itself is real and immediately navigable via `switch_branch`; the summary is a follow-up.
    """
    turn = await _load_turn(store, user_id=user.id, thread_id=thread_id, turn_id=turn_id)
    _require_user_turn(turn)
    sibling_parent_id = _first_text_message_parent_id(turn)

    thread = await _require_owned_thread(store, user_id=user.id, thread_id=thread_id)
    ctx = tool_context_for(user, state)
    # Gate BEFORE any write on this request's session: `enforce_pre_turn_gate` opens its own
    # session from the shared factory to atomically bump the usage counter, and SQLite allows
    # only one writer at a time — an uncommitted write already held on this session's connection
    # would deadlock against that second connection (observed as "database is locked").
    await enforce_pre_turn_gate(state, session, user)

    new_user_turn = await store.append_turn(
        user_id=user.id,
        thread_id=thread_id,
        role=TurnRole.USER,
        blocks=[(MessageBlockType.TEXT, TextBlock(text=body.message))],
        parent_message_id=sibling_parent_id,
    )

    new_assistant_turn_id = str(uuid.uuid4())
    personality = PersonalityContext(
        thread_override=thread.personality_override, user_preference=user.personality_id
    )
    return start_turn_stream(
        state=state,
        store=store,
        session=session,
        user_id=user.id,
        thread_id=thread_id,
        turn_id=new_assistant_turn_id,
        message=body.message,
        ctx=ctx,
        personality=personality,
        answer_parent_message_id=new_user_turn.messages[-1].id,
    )


__all__ = ["router"]
