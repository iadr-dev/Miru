"""Conversation thread CRUD + history, over the already-built `conversation.store`.

Every route is user-scoped: the store's own methods take `user_id` and filter on it, so one
user can never read or mutate another's threads (the per-user isolation invariant). This slice
covers create / list / get-with-history / delete; message-action routes (feedback, regenerate,
edit, fork, branch-switch, steer) are a deliberate later pass — they depend on turn-lifecycle
plumbing that this first HTTP slice does not yet expose.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import User
from api.dependencies import get_session, require_user
from api.errors import ApiError
from conversation.models import Thread
from conversation.store import (
    ConversationStore,
    SqlAlchemyConversationStore,
    ThreadNotFoundError,
)

router = APIRouter(tags=["threads"])


def _store(session: AsyncSession = Depends(get_session)) -> ConversationStore:
    return SqlAlchemyConversationStore(session)


class CreateThreadRequest(BaseModel):
    title: str = Field(default="", max_length=200)


@router.post("/threads", status_code=status.HTTP_201_CREATED)
async def create_thread(
    body: CreateThreadRequest,
    user: User = Depends(require_user),
    store: ConversationStore = Depends(_store),
) -> Thread:
    return await store.create_thread(user_id=user.id, title=body.title)


class ThreadsResponse(BaseModel):
    threads: list[Thread]


@router.get("/threads")
async def list_threads(
    include_archived: bool = Query(False),
    user: User = Depends(require_user),
    store: ConversationStore = Depends(_store),
) -> ThreadsResponse:
    threads = await store.list_threads(user_id=user.id, include_archived=include_archived)
    return ThreadsResponse(threads=threads)


@router.get("/threads/{thread_id}")
async def get_thread(
    thread_id: str,
    user: User = Depends(require_user),
    store: ConversationStore = Depends(_store),
) -> Thread:
    try:
        return await store.get_thread(user_id=user.id, thread_id=thread_id)
    except ThreadNotFoundError as exc:
        raise _not_found(thread_id) from exc


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: str,
    user: User = Depends(require_user),
    store: ConversationStore = Depends(_store),
) -> None:
    try:
        await store.delete_thread(user_id=user.id, thread_id=thread_id)
    except ThreadNotFoundError as exc:
        raise _not_found(thread_id) from exc


def _not_found(thread_id: str) -> ApiError:
    return ApiError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="thread_not_found",
        message=f"Thread {thread_id} not found.",
        retryable=False,
    )


__all__ = ["router"]
