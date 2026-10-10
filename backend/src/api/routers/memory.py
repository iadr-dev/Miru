"""Long-term memory CRUD + the first-class watchlist rail.

Memory routes wrap `memory.store.SqlAlchemyMemoryStore` — the store owns the governance
write-path scan (credentials / raw payloads / trading language are refused there, surfaced here
as 422), and it refuses a `watchlist`-typed write outright (watchlists are relational now).

Watchlist routes wrap `memory.watchlists.WatchlistStore`, whose lexorank ordering + optimistic
`version` are the real mechanism this layer only threads HTTP through. Group mutations honor
`If-Match: <version>`: a mismatch is a 409 so a second device refetches rather than clobbering.
All watchlist data is public user data (no tier gate); only the live-quote column elsewhere
inherits the Premium+ gate.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import User
from api.dependencies import get_session, require_user
from api.errors import ApiError
from memory.models import Memory, MemoryGovernanceError, MemoryScope, MemoryType
from memory.settings import memory_enabled, set_memory_enabled
from memory.store import SqlAlchemyMemoryStore
from memory.watchlists import (
    StaleWatchlistVersionError,
    WatchlistColor,
    WatchlistGroup,
    WatchlistItem,
    WatchlistItemNotFoundError,
    WatchlistNotFoundError,
    WatchlistStore,
)
from schemas.enums import Market

router = APIRouter(tags=["memory"])

_HTTP_422_UNPROCESSABLE = 422
# The write-path memory types a client may set — `watchlist` is deliberately excluded (the
# relational watchlist store is the writer now; the memory store refuses it anyway).
_WRITABLE_MEMORY_TYPES = frozenset(
    {MemoryType.PREFERENCE, MemoryType.FINDING, MemoryType.ENTITY_NOTE}
)


def _memory_store(session: AsyncSession = Depends(get_session)) -> SqlAlchemyMemoryStore:
    return SqlAlchemyMemoryStore(session)


def _watchlist_store(session: AsyncSession = Depends(get_session)) -> WatchlistStore:
    return WatchlistStore(session)


# ---- memory --------------------------------------------------------------------------------


def _searchable_text(memory: Memory) -> str:
    values = [memory.key, *(str(v) for v in memory.content.values())]
    return " ".join(values).lower()


@router.get("/memory")
async def list_memory(
    scope: MemoryScope | None = Query(None),
    q: str | None = Query(None, max_length=100),
    user: User = Depends(require_user),
    store: SqlAlchemyMemoryStore = Depends(_memory_store),
) -> dict[str, list[Memory]]:
    """The caller's memories, most recently changed first; `q` keeps the ones whose key or text
    contains it."""
    memories = await store.list(user_id=user.id, scope=scope)
    needle = (q or "").strip().lower()
    if needle:
        memories = [m for m in memories if needle in _searchable_text(m)]
    memories.sort(key=lambda m: m.updated_at, reverse=True)
    return {"memories": memories}


class MemorySettings(BaseModel):
    enabled: bool


@router.get("/memory/settings")
async def get_memory_settings(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
) -> MemorySettings:
    return MemorySettings(enabled=await memory_enabled(session, user_id=user.id))


@router.put("/memory/settings")
async def put_memory_settings(
    body: MemorySettings,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> MemorySettings:
    """Pause or resume long-term memory. Pausing keeps what is stored; it just stops being used."""
    await set_memory_enabled(session, user_id=user.id, enabled=body.enabled)
    return body


class ClearMemoryResponse(BaseModel):
    deleted: int


@router.delete("/memory")
async def clear_memory(
    scope: MemoryScope | None = Query(None),
    user: User = Depends(require_user),
    store: SqlAlchemyMemoryStore = Depends(_memory_store),
) -> ClearMemoryResponse:
    """Forget everything (or one scope) in one go."""
    return ClearMemoryResponse(deleted=await store.delete_all(user_id=user.id, scope=scope))


class UpdateMemoryRequest(BaseModel):
    content: dict[str, object]


@router.patch("/memory/{memory_id}")
async def update_memory(
    memory_id: str,
    body: UpdateMemoryRequest,
    user: User = Depends(require_user),
    store: SqlAlchemyMemoryStore = Depends(_memory_store),
) -> Memory:
    """Edit a memory's content in place (its key and type stay)."""
    try:
        updated = await store.update(user_id=user.id, memory_id=memory_id, content=body.content)
    except MemoryGovernanceError as exc:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="memory_rejected",
            message=str(exc),
            retryable=False,
        ) from exc
    if updated is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="memory_not_found",
            message=f"Memory {memory_id} not found.",
            retryable=False,
        )
    return updated


class UpsertMemoryRequest(BaseModel):
    scope: MemoryScope = MemoryScope.USER
    type: MemoryType = MemoryType.PREFERENCE
    key: str = Field(min_length=1, max_length=200)
    content: dict[str, object] = Field(default_factory=dict)
    thread_id: str | None = None
    source_ref: str | None = None


@router.post("/memory", status_code=status.HTTP_201_CREATED)
async def upsert_memory(
    body: UpsertMemoryRequest,
    user: User = Depends(require_user),
    store: SqlAlchemyMemoryStore = Depends(_memory_store),
) -> Memory:
    if body.type not in _WRITABLE_MEMORY_TYPES:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="invalid_memory_type",
            message="Only preference/finding/entity_note memories can be written here.",
            retryable=False,
        )
    try:
        return await store.upsert(
            user_id=user.id,
            scope=body.scope,
            type=body.type,
            key=body.key,
            content=body.content,
            thread_id=body.thread_id,
            source_ref=body.source_ref,
        )
    except MemoryGovernanceError as exc:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="memory_rejected",
            message=str(exc),
            retryable=False,
        ) from exc


@router.delete("/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    user: User = Depends(require_user),
    store: SqlAlchemyMemoryStore = Depends(_memory_store),
) -> None:
    deleted = await store.delete(user_id=user.id, memory_id=memory_id)
    if not deleted:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="memory_not_found",
            message=f"Memory {memory_id} not found.",
            retryable=False,
        )


# ---- watchlists ----------------------------------------------------------------------------

_DEFAULT_ASSET_CLASS = "stock"


def _wl_not_found(watchlist_id: str) -> ApiError:
    return ApiError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="watchlist_not_found",
        message=f"Watchlist {watchlist_id} not found.",
        retryable=False,
    )


def _stale_version(watchlist_id: str) -> ApiError:
    return ApiError(
        status_code=status.HTTP_409_CONFLICT,
        code="stale_version",
        message=f"Watchlist {watchlist_id} was modified by another device — refetch and retry.",
        retryable=True,
    )


class WatchlistGroupView(BaseModel):
    group: WatchlistGroup
    items: tuple[WatchlistItem, ...] = Field(default_factory=tuple)


_PRESET_MARKET_ASSET_CLASS_COMBOS: tuple[tuple[Market, str], ...] = (
    (Market.TW, "stock"),
    (Market.US, "stock"),
    (Market.TW, "etf"),
)


@router.get("/watchlists")
async def list_watchlists(
    market: Market | None = Query(None),
    user: User = Depends(require_user),
    store: WatchlistStore = Depends(_watchlist_store),
) -> dict[str, list[WatchlistGroupView]]:
    # Seed starter presets for any (market, asset_class) combo this user has zero groups in yet
    # — a no-op per combo once real groups exist there (own edits/deletions never get
    # overwritten). Runs before the read below so a brand-new user's very first `GET` already
    # sees their presets, never a cold-empty rail.
    for preset_market, preset_asset_class in _PRESET_MARKET_ASSET_CLASS_COMBOS:
        if market is not None and preset_market is not market:
            continue
        await store.seed_default_presets(
            user_id=user.id, market=preset_market, asset_class=preset_asset_class
        )
    groups = await store.list_groups(user.id)
    views: list[WatchlistGroupView] = []
    for group in groups:
        if market is not None and group.market is not market:
            continue
        items = await store.list_items(user_id=user.id, watchlist_id=group.id)
        views.append(WatchlistGroupView(group=group, items=tuple(items)))
    return {"watchlists": views}


class CreateWatchlistRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    market: Market
    asset_class: str = _DEFAULT_ASSET_CLASS


@router.post("/watchlists", status_code=status.HTTP_201_CREATED)
async def create_watchlist(
    body: CreateWatchlistRequest,
    user: User = Depends(require_user),
    store: WatchlistStore = Depends(_watchlist_store),
) -> WatchlistGroup:
    return await store.create_group(
        user_id=user.id, name=body.name, market=body.market, asset_class=body.asset_class
    )


class PatchWatchlistRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    # A palette key sets the label colour; "none" clears it; omitted leaves it alone.
    color: WatchlistColor | Literal["none"] | None = None
    after_id: str | None = None
    before_id: str | None = None


@router.patch("/watchlists/{watchlist_id}")
async def patch_watchlist(
    watchlist_id: str,
    body: PatchWatchlistRequest,
    user: User = Depends(require_user),
    store: WatchlistStore = Depends(_watchlist_store),
    if_match: int | None = Header(default=None, alias="If-Match"),
) -> WatchlistGroup:
    """Rename, recolour and/or reorder a group. `If-Match: <version>` enforces optimistic
    concurrency."""
    try:
        group = await store.get_group(user_id=user.id, watchlist_id=watchlist_id)
        if body.name is not None:
            group = await store.rename_group(
                user_id=user.id,
                watchlist_id=watchlist_id,
                name=body.name,
                expected_version=if_match,
            )
        if body.color is not None:
            group = await store.set_group_color(
                user_id=user.id,
                watchlist_id=watchlist_id,
                color=None if body.color == "none" else body.color,
                expected_version=if_match if body.name is None else None,
            )
        if body.after_id is not None or body.before_id is not None:
            group = await store.reorder_group(
                user_id=user.id,
                watchlist_id=watchlist_id,
                after_id=body.after_id,
                before_id=body.before_id,
                expected_version=if_match if body.name is None and body.color is None else None,
            )
        return group
    except WatchlistNotFoundError as exc:
        raise _wl_not_found(watchlist_id) from exc
    except StaleWatchlistVersionError as exc:
        raise _stale_version(watchlist_id) from exc


@router.delete("/watchlists/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist(
    watchlist_id: str,
    user: User = Depends(require_user),
    store: WatchlistStore = Depends(_watchlist_store),
) -> None:
    try:
        await store.delete_group(user_id=user.id, watchlist_id=watchlist_id)
    except WatchlistNotFoundError as exc:
        raise _wl_not_found(watchlist_id) from exc


class ReorderWatchlistsRequest(BaseModel):
    ordered_ids: tuple[str, ...] = Field(min_length=1)


@router.post("/watchlists/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_watchlists(
    body: ReorderWatchlistsRequest,
    user: User = Depends(require_user),
    store: WatchlistStore = Depends(_watchlist_store),
) -> None:
    try:
        await store.reorder_groups(user_id=user.id, ordered_ids=body.ordered_ids)
    except WatchlistNotFoundError as exc:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="watchlist_not_found",
            message="One or more watchlists in ordered_ids do not exist.",
            retryable=False,
        ) from exc


@router.get("/watchlists/{watchlist_id}/items")
async def list_watchlist_items(
    watchlist_id: str,
    user: User = Depends(require_user),
    store: WatchlistStore = Depends(_watchlist_store),
) -> dict[str, list[WatchlistItem]]:
    try:
        return {"items": await store.list_items(user_id=user.id, watchlist_id=watchlist_id)}
    except WatchlistNotFoundError as exc:
        raise _wl_not_found(watchlist_id) from exc


class PutItemRequest(BaseModel):
    note: str | None = None


@router.put("/watchlists/{watchlist_id}/items/{symbol}")
async def add_watchlist_item(
    watchlist_id: str,
    symbol: str,
    body: PutItemRequest,
    user: User = Depends(require_user),
    store: WatchlistStore = Depends(_watchlist_store),
) -> WatchlistItem:
    """Star toggle — add. Idempotent: a repeat add returns the existing row, never a duplicate."""
    try:
        return await store.add_item(
            user_id=user.id, watchlist_id=watchlist_id, symbol=symbol, note=body.note
        )
    except WatchlistNotFoundError as exc:
        raise _wl_not_found(watchlist_id) from exc


@router.delete("/watchlists/{watchlist_id}/items/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_watchlist_item(
    watchlist_id: str,
    symbol: str,
    user: User = Depends(require_user),
    store: WatchlistStore = Depends(_watchlist_store),
) -> None:
    """Star toggle — remove. Idempotent: 204 whether or not the symbol was starred."""
    await store.remove_item(user_id=user.id, watchlist_id=watchlist_id, symbol=symbol)


class PatchItemRequest(BaseModel):
    note: str | None = None
    clear_note: bool = False


@router.patch("/watchlists/{watchlist_id}/items/{item_id}")
async def patch_watchlist_item(
    watchlist_id: str,
    item_id: str,
    body: PatchItemRequest,
    user: User = Depends(require_user),
    store: WatchlistStore = Depends(_watchlist_store),
) -> WatchlistItem:
    try:
        note = None if body.clear_note else body.note
        return await store.set_item_note(
            user_id=user.id, watchlist_id=watchlist_id, item_id=item_id, note=note
        )
    except WatchlistItemNotFoundError as exc:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="watchlist_item_not_found",
            message=f"Watchlist item {item_id} not found.",
            retryable=False,
        ) from exc


class MoveItemRequest(BaseModel):
    target_watchlist_id: str = Field(min_length=1)
    after_id: str | None = None


@router.post("/watchlists/{watchlist_id}/items/{item_id}/move")
async def move_watchlist_item(
    watchlist_id: str,
    item_id: str,
    body: MoveItemRequest,
    user: User = Depends(require_user),
    store: WatchlistStore = Depends(_watchlist_store),
) -> WatchlistItem:
    del watchlist_id  # the item is located by (user, item_id); path group is contextual only
    try:
        return await store.move_item(
            user_id=user.id,
            item_id=item_id,
            target_watchlist_id=body.target_watchlist_id,
            after_id=body.after_id,
        )
    except WatchlistNotFoundError as exc:
        raise _wl_not_found(body.target_watchlist_id) from exc
    except WatchlistItemNotFoundError as exc:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="watchlist_item_not_found",
            message=f"Watchlist item {item_id} not found.",
            retryable=False,
        ) from exc


__all__ = ["router"]
