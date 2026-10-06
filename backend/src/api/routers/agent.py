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
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import User
from api.attachments import AttachmentInput, process_attachments
from api.dependencies import (
    AppState,
    get_session,
    get_settings_dep,
    get_state,
    require_user,
    tool_context_for,
)
from api.errors import ApiError
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
    model: str | None = Field(default=None, max_length=200)
    # Pro-only multi-step cited web research instead of a normal chat reply (see `research/`).
    deep_research: bool = False


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
    await enforce_pre_turn_gate(state, session, user, deep_research=body.deep_research)
    thread = await _resolve_thread(store, user_id=user.id, thread_id=body.thread_id)
    if body.model is not None and body.model != thread.model_override:
        thread = await store.update_thread(
            user_id=user.id, thread_id=thread.id, model_override=body.model
        )
    thread_id = thread.id

    augmented_message, images, attachment_blocks = await process_attachments(
        body.attachments, message=body.message, settings=settings
    )

    blocks: list[tuple[MessageBlockType, MessageBlockContent]] = [
        (MessageBlockType.TEXT, TextBlock(text=body.message)),
        *attachment_blocks,
    ]
    user_turn = await store.append_turn(
        user_id=user.id,
        thread_id=thread_id,
        role=TurnRole.USER,
        blocks=blocks,
        parent_message_id=await current_leaf(store, thread_id),
        idempotency_key=idempotency_key,
    )

    # The streaming correlation id must be the persisted turn's own id — `stream_events.turn_id`
    # has a hard FK to `turns.id`, so a durable-log write for any other id silently no-ops.
    turn_id = user_turn.id
    personality = PersonalityContext(
        thread_override=thread.personality_override, user_preference=user.personality_id
    )
    return start_turn_stream(
        state=state,
        store=store,
        session=session,
        user_id=user.id,
        thread_id=thread_id,
        turn_id=turn_id,
        message=augmented_message,
        ctx=ctx,
        personality=personality,
        answer_parent_message_id=user_turn.messages[-1].id,
        model_override=thread.model_override,
        web_search_enabled=body.web_search,
        locale=body.locale,
        images=images,
        deep_research=body.deep_research,
    )


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
