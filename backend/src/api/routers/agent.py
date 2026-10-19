"""`POST /agent/query` — the core chat entrypoint, wired to the already-built `Orchestrator`.

Flow of one turn:
1. Resolve the (required) signed-in user → the per-request `ToolContext` (entitlement + TLS).
2. Auto-create a thread when none is supplied; persist the user message.
3. Hand off to `api.turn_driver.start_turn_stream` — the shared seam `regenerate`/`edit` also call
   — which runs `Orchestrator.run()` in a background task against a fresh in-process `EventBus`,
   while the SSE response subscribes to that bus and streams every frame the orchestrator
   publishes (`route.decided`, `agent.spawned`, `synthesis.start`, …) in real time.
4. On completion, the driver persists the assistant answer, then publishes a final `message`
   frame + `turn.end` so the stream terminates cleanly.

Governance is not re-implemented here: the answer text is produced by the orchestrator, whose
lanes and synthesis each run `guardrails.check_output`, and whose every tool call routes through
`tools.base.dispatch_tool`'s sponsor gate via the `ToolContext` this router hands it. This router
adds no tool path of its own, so it cannot bypass either hook.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.entitlements import chat_limit_for
from accounts.models import User
from api.attachments import AttachmentInput, process_attachments
from api.byok import resolve_byok, validated_model_pick
from api.dependencies import (
    AppState,
    get_session,
    get_settings_dep,
    get_state,
    require_user,
    tool_context_for,
)
from api.errors import ApiError
from api.personality_choice import personality_patch
from api.turn_driver import current_leaf, enforce_pre_turn_gate, start_turn_stream
from config.settings import Settings
from conversation.models import MessageBlockContent, MessageBlockType, TextBlock, Thread, TurnRole
from conversation.store import (
    ConversationStore,
    SqlAlchemyConversationStore,
    ThreadNotFoundError,
)
from orchestrator.orchestrator import PersonalityContext

router = APIRouter(tags=["agent"])


class AgentQueryRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    thread_id: str | None = None
    web_search: bool = False
    locale: str = "zh-TW"
    attachments: tuple[AttachmentInput, ...] = Field(default_factory=tuple)
    # The composer's model pick, sent WITH the message so the very first turn of a new chat honors
    # it (a separate PATCH /threads can only land after the thread exists, i.e. after this turn).
    # Only a BYOK caller's pick counts (validated against `GET /models`); for everyone else the
    # platform profile decides the model and this field is ignored.
    model: str | None = Field(default=None, max_length=200)
    # This chat's 語氣 (a preset id, or "default" to follow the account setting), sent WITH the
    # message for the same reason as `model`: a new chat has no thread to PATCH yet.
    personality: str | None = Field(default=None, max_length=50)
    # Pro-only multi-step cited web research instead of a normal chat reply (see `research/`).
    deep_research: bool = False


_MAX_IDEMPOTENCY_KEY_LENGTH = 128


def _check_idempotency_key(key: str) -> None:
    if not key.strip() or len(key) > _MAX_IDEMPOTENCY_KEY_LENGTH or not key.isprintable():
        raise ApiError(
            status_code=422,
            code="invalid_request",
            message=(
                f"Idempotency-Key must be 1-{_MAX_IDEMPOTENCY_KEY_LENGTH} printable characters."
            ),
            retryable=False,
        )


async def _reject_duplicate(store: ConversationStore, user_id: str, key: str) -> None:
    """409 with the existing turn's ids in headers when this caller already used `key`, else
    return. The client reads the thread (the answer, once saved) or attaches to
    `GET /threads/{id}/stream` (while it runs)."""
    existing = await store.turn_for_idempotency_key(user_id=user_id, idempotency_key=key)
    if existing is None:
        return
    thread_id, turn_id = existing
    raise ApiError(
        status_code=status.HTTP_409_CONFLICT,
        code="duplicate_request",
        message="This message was already sent; it is not started again.",
        retryable=False,
        headers={"X-Thread-Id": thread_id, "X-Turn-Id": turn_id},
    )


def _store(session: AsyncSession = Depends(get_session)) -> ConversationStore:
    return SqlAlchemyConversationStore(session)


@router.post("/agent/query")
async def agent_query(
    body: AgentQueryRequest,
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: ConversationStore = Depends(_store),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StreamingResponse:
    ctx = tool_context_for(user, state)
    # A retry of a message that was already accepted (a double-click, a request repeated after a
    # dropped connection) must not start a second turn or take a second chat from the day's
    # allowance: answer before the gate, with where the first one is.
    if idempotency_key is not None:
        _check_idempotency_key(idempotency_key)
        await _reject_duplicate(store, user.id, idempotency_key)
    # Decide BYOK first (it needs the model the turn will use), then let the gate act on that one
    # decision - a refusal here happens before anything is claimed.
    picked = await validated_model_pick(session, settings, user_id=user.id, pick=body.model)
    model = await _model_for_turn(store, user.id, body, picked)
    byok = await resolve_byok(
        session,
        settings,
        user_id=user.id,
        model_override=model,
        default_model=user.default_model,
    )
    await enforce_pre_turn_gate(
        state, session, user, deep_research=body.deep_research, byok=byok is not None
    )
    # End the read-only transaction the sign-in and the checks above opened (it holds share locks
    # on users/subscriptions) before the one slow step here, so nothing is held while it runs.
    await session.commit()
    augmented_message, images, attachment_blocks = await process_attachments(
        body.attachments, message=body.message, settings=settings
    )

    thread = await _resolve_thread(store, user_id=user.id, thread_id=body.thread_id)
    if picked is not None and picked != thread.model_override:
        thread = await store.update_thread(
            user_id=user.id, thread_id=thread.id, model_override=picked
        )
    clear_tone, tone = personality_patch(state, body.personality)
    if clear_tone or (tone is not None and tone != thread.personality_override):
        thread = await store.update_thread(
            user_id=user.id,
            thread_id=thread.id,
            personality_override=tone,
            clear_personality_override=clear_tone,
        )
    thread_id = thread.id

    blocks: list[tuple[MessageBlockType, MessageBlockContent]] = [
        (MessageBlockType.TEXT, TextBlock(text=body.message)),
        *attachment_blocks,
    ]
    try:
        user_turn = await store.append_turn(
            user_id=user.id,
            thread_id=thread_id,
            role=TurnRole.USER,
            blocks=blocks,
            parent_message_id=await current_leaf(store, thread_id),
            idempotency_key=idempotency_key,
            mode="deep_research" if body.deep_research else None,
        )
    except IntegrityError:
        # Two copies of the same message arrived together and both passed the check above; the
        # unique constraint let one through. Undo this one (the thread it may have created too),
        # give back the chat it claimed, and answer as for any duplicate.
        await session.rollback()
        if idempotency_key is None:
            raise
        await state.usage_store.refund_daily_chat(
            user_id=user.id, limit=chat_limit_for(user, byok_active=byok is not None)
        )
        await _reject_duplicate(store, user.id, idempotency_key)
        raise
    answer_parent_message_id = user_turn.messages[-1].id
    # The question is durable before the answer starts, and the connection, with the lock on the
    # thread row the append took, goes back now: the turn holds no connection while it runs.
    await session.commit()

    # The streaming correlation id must be the persisted turn's own id — `stream_events.turn_id`
    # has a hard FK to `turns.id`, so a durable-log write for any other id silently no-ops.
    turn_id = user_turn.id
    personality = PersonalityContext(
        thread_override=thread.personality_override, user_preference=user.personality_id
    )
    return start_turn_stream(
        state=state,
        user_id=user.id,
        thread_id=thread_id,
        turn_id=turn_id,
        message=augmented_message,
        ctx=ctx,
        personality=personality,
        answer_parent_message_id=answer_parent_message_id,
        # a stored override only means something on the caller's own key
        model_override=thread.model_override if byok is not None else None,
        web_search_enabled=body.web_search,
        locale=body.locale,
        images=images,
        deep_research=body.deep_research,
        byok=byok,
    )


async def _model_for_turn(
    store: ConversationStore, user_id: str, body: AgentQueryRequest, picked: str | None
) -> str | None:
    """The model override this request will end up with: the (validated) composer pick, else the
    existing thread's. A missing thread is left for `_resolve_thread` to report (404)."""
    if picked is not None:
        return picked
    if body.thread_id is None:
        return None
    try:
        return (await store.get_thread(user_id=user_id, thread_id=body.thread_id)).model_override
    except ThreadNotFoundError:
        return None


async def _resolve_thread(
    store: ConversationStore, *, user_id: str, thread_id: str | None
) -> Thread:
    if thread_id is not None:
        try:
            return await store.get_thread(user_id=user_id, thread_id=thread_id)
        except ThreadNotFoundError as exc:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="thread_not_found",
                message=f"Thread {thread_id} not found.",
                retryable=False,
            ) from exc
    return await store.create_thread(user_id=user_id)


__all__ = ["router"]
