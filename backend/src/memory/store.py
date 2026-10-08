"""`MemoryStore` — the SQLAlchemy-backed long-term memory layer (`memories` table).

Governance is enforced here, not just documented: `upsert` re-validates `content` through the
same scan `memory.models.Memory` runs, and additionally refuses `type=MemoryType.WATCHLIST` on
every new write (that type is legacy-read-only — the relational watchlist store is the writer
now). Both checks raise `MemoryGovernanceError`; the caller never gets a silently-dropped or
silently-mutated write.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import MemoryRow
from memory.models import (
    Memory,
    MemoryGovernanceError,
    MemoryScope,
    MemoryType,
    scan_content_for_governance_violations,
)

logger = structlog.get_logger(__name__)


@runtime_checkable
class MemoryStore(Protocol):
    async def upsert(  # noqa: PLR0913 — one field per persisted `Memory` dimension
        self,
        *,
        user_id: str,
        scope: MemoryScope,
        type: MemoryType,  # noqa: A002 — mirrors the domain field name (`Memory.type`)
        key: str,
        content: dict[str, object],
        thread_id: str | None = None,
        source_ref: str | None = None,
    ) -> Memory: ...

    async def list(
        self, *, user_id: str, scope: MemoryScope | None = None, thread_id: str | None = None
    ) -> list[Memory]: ...

    async def delete(self, *, user_id: str, memory_id: str) -> bool: ...

    async def update(
        self, *, user_id: str, memory_id: str, content: dict[str, object]
    ) -> Memory | None: ...

    async def delete_all(self, *, user_id: str, scope: MemoryScope | None = None) -> int: ...

    async def mark_used(self, *, user_id: str, memory_ids: Sequence[str]) -> None: ...


def _row_to_memory(row: MemoryRow) -> Memory:
    return Memory(
        id=row.id,
        user_id=row.user_id,
        scope=MemoryScope(row.scope),
        thread_id=row.thread_id,
        type=MemoryType(row.type),
        key=row.key,
        content=row.content,
        source_ref=row.source_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_used_at=row.last_used_at,
        use_count=row.use_count or 0,
    )


class SqlAlchemyMemoryStore:
    """`MemoryStore` over one `AsyncSession`. The caller owns the session/transaction
    boundary (per-request unit of work, per `db/base.py::session_scope`) — this class never
    opens its own session, so it composes cleanly with the isolation-scoping wrapper other
    store protocols in this codebase use."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(  # noqa: PLR0913 — matches the Protocol's own shape above
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
        if type is MemoryType.WATCHLIST:
            raise MemoryGovernanceError(
                "type=watchlist is retired as a write target — use the relational watchlist "
                "store (memory.watchlists.WatchlistStore) instead"
            )
        # Run the same governance scan `Memory` itself runs on construction, but explicitly
        # and first — raising `MemoryGovernanceError` directly here (rather than letting the
        # `Memory(...)` construction below raise it wrapped in a pydantic `ValidationError`)
        # keeps this store's public contract "governance violations raise
        # `MemoryGovernanceError`", not "raise a pydantic error that happens to wrap one".
        violation = scan_content_for_governance_violations(content)
        if violation is not None:
            raise MemoryGovernanceError(violation)

        existing = await self._session.execute(
            select(MemoryRow).where(
                MemoryRow.user_id == user_id,
                MemoryRow.scope == scope.value,
                MemoryRow.type == type.value,
                MemoryRow.key == key,
            )
        )
        row = existing.scalar_one_or_none()
        now = datetime.now(UTC)
        if row is None:
            row = MemoryRow(
                id=str(uuid.uuid4()),
                user_id=user_id,
                scope=scope.value,
                thread_id=thread_id,
                type=type.value,
                key=key,
                content=content,
                source_ref=source_ref,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
        else:
            row.content = content
            row.source_ref = source_ref
            row.thread_id = thread_id
            row.updated_at = now
        await self._session.flush()
        logger.info("memory_upserted", user_id=user_id, scope=scope.value, type=type.value, key=key)
        return _row_to_memory(row)

    async def list(
        self, *, user_id: str, scope: MemoryScope | None = None, thread_id: str | None = None
    ) -> list[Memory]:
        stmt = select(MemoryRow).where(MemoryRow.user_id == user_id)
        if scope is not None:
            stmt = stmt.where(MemoryRow.scope == scope.value)
        if thread_id is not None:
            stmt = stmt.where(MemoryRow.thread_id == thread_id)
        result = await self._session.execute(stmt)
        return [_row_to_memory(row) for row in result.scalars().all()]

    async def delete(self, *, user_id: str, memory_id: str) -> bool:
        """Deletes immediately, honoring the "no shadow copies" rule. Checks existence first
        (rather than branching on `CursorResult.rowcount`, whose type SQLAlchemy's async
        engine cannot narrow past `Any` at this call shape) so the return value stays a plain,
        fully-typed `bool`."""
        existing = await self._session.execute(
            select(MemoryRow.id).where(MemoryRow.user_id == user_id, MemoryRow.id == memory_id)
        )
        if existing.scalar_one_or_none() is None:
            return False
        await self._session.execute(
            delete(MemoryRow).where(MemoryRow.user_id == user_id, MemoryRow.id == memory_id)
        )
        await self._session.flush()
        logger.info("memory_deleted", user_id=user_id, memory_id=memory_id)
        return True

    async def update(
        self, *, user_id: str, memory_id: str, content: dict[str, object]
    ) -> Memory | None:
        """Replace one memory's content (its key and type are its identity and stay). The same
        governance scan as `upsert` applies; `None` when the memory is not the caller's."""
        violation = scan_content_for_governance_violations(content)
        if violation is not None:
            raise MemoryGovernanceError(violation)
        result = await self._session.execute(
            select(MemoryRow).where(MemoryRow.user_id == user_id, MemoryRow.id == memory_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        row.content = content
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        logger.info("memory_updated", user_id=user_id, memory_id=memory_id)
        return _row_to_memory(row)

    async def delete_all(self, *, user_id: str, scope: MemoryScope | None = None) -> int:
        """Forget everything (or one scope) at once; returns how many were removed."""
        stmt = select(MemoryRow.id).where(MemoryRow.user_id == user_id)
        if scope is not None:
            stmt = stmt.where(MemoryRow.scope == scope.value)
        ids = list((await self._session.execute(stmt)).scalars().all())
        if ids:
            await self._session.execute(
                delete(MemoryRow).where(MemoryRow.user_id == user_id, MemoryRow.id.in_(ids))
            )
            await self._session.flush()
        logger.info("memory_cleared", user_id=user_id, count=len(ids))
        return len(ids)

    async def mark_used(self, *, user_id: str, memory_ids: Sequence[str]) -> None:
        """Stamp the memories recall just put in front of the model."""
        if not memory_ids:
            return
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(MemoryRow).where(MemoryRow.user_id == user_id, MemoryRow.id.in_(memory_ids))
        )
        for row in result.scalars().all():
            row.last_used_at = now
            row.use_count = (row.use_count or 0) + 1
        await self._session.flush()


__all__ = ["MemoryStore", "SqlAlchemyMemoryStore"]
