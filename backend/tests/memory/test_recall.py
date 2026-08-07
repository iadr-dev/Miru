"""`memory.recall.recall` — scope+key+lightweight-semantic-match selection and the compact
memory block rendering, including the watchlist snapshot projection."""

from __future__ import annotations

from datetime import UTC, datetime

from memory.models import Memory, MemoryScope, MemoryType
from memory.recall import recall
from memory.watchlists import WatchlistSnapshotGroup
from schemas.enums import Market


class _StubMemoryStore:
    """`recall` only ever calls `list` — `upsert`/`delete` below exist solely so this class
    satisfies the full `MemoryStore` Protocol shape structurally; they are never invoked."""

    def __init__(self, memories: list[Memory]) -> None:
        self._memories = memories

    async def list(
        self, *, user_id: str, scope: MemoryScope | None = None, thread_id: str | None = None
    ) -> list[Memory]:
        return self._memories

    async def upsert(  # noqa: PLR0913
        self,
        *,
        user_id: str,
        scope: MemoryScope,
        type: MemoryType,  # noqa: A002
        key: str,
        content: dict[str, object],
        thread_id: str | None = None,
        source_ref: str | None = None,
    ) -> Memory:
        raise NotImplementedError

    async def delete(self, *, user_id: str, memory_id: str) -> bool:
        raise NotImplementedError


class _StubWatchlistStore:
    def __init__(self, groups: list[WatchlistSnapshotGroup]) -> None:
        self._groups = groups

    async def snapshot(self, user_id: str) -> list[WatchlistSnapshotGroup]:
        return self._groups

    async def watches(self, *, user_id: str, symbol: str) -> bool:
        return any(symbol in group.symbols for group in self._groups)


def _memory(key: str, content: dict[str, object], **overrides: object) -> Memory:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "id": f"mem-{key}",
        "user_id": "user-1",
        "scope": MemoryScope.USER,
        "type": MemoryType.PREFERENCE,
        "key": key,
        "content": content,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return Memory.model_validate(base)


async def test_recall_returns_none_when_nothing_relevant() -> None:
    result = await recall(
        memory_store=_StubMemoryStore([]),
        watchlist_store=None,
        user_id="user-1",
        thread_id=None,
        query="what's the weather",
    )
    assert result is None


async def test_recall_includes_matching_memory_by_token_overlap() -> None:
    memory = _memory("chip flow note", {"observation": "8-day foreign net-buy streak on 2330"})
    result = await recall(
        memory_store=_StubMemoryStore([memory]),
        watchlist_store=None,
        user_id="user-1",
        thread_id=None,
        query="tell me about chip flow",
    )
    assert result is not None
    assert "chip flow note" in result
    assert "background context" in result.lower()


async def test_recall_renders_watchlist_snapshot() -> None:
    groups = [
        WatchlistSnapshotGroup(
            name="核心", market=Market.TW, asset_class="stock", symbols=("2330",)
        )
    ]
    result = await recall(
        memory_store=_StubMemoryStore([]),
        watchlist_store=_StubWatchlistStore(groups),
        user_id="user-1",
        thread_id=None,
        query="what's on my watchlist",
    )
    assert result is not None
    assert "watchlist '核心'" in result
    assert "2330" in result


async def test_recall_ranks_thread_scoped_memory_above_user_scoped_at_equal_overlap() -> None:
    user_memory = _memory("dividend note", {"observation": "dividend note for stock"})
    thread_memory = _memory(
        "dividend note",
        {"observation": "dividend note for stock"},
        id="mem-thread",
        scope=MemoryScope.THREAD,
        thread_id="thread-1",
    )
    result = await recall(
        memory_store=_StubMemoryStore([user_memory, thread_memory]),
        watchlist_store=None,
        user_id="user-1",
        thread_id="thread-1",
        query="dividend note",
        max_memories=1,
    )
    assert result is not None
    assert "mem-thread" not in result  # rendered line doesn't leak the raw id
    assert "dividend note" in result
