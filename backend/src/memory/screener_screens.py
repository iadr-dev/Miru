"""Saved screener screens (README.md §6 `SavedScreens`) — CRUD over `screener_screens`. Mirrors
`memory/watchlists.py`'s shape (the caller owns the session/transaction boundary, every read
reconstructs a real domain model rather than a raw ORM row), minus the lexorank/version machinery
that module needs for its drag-and-drop rail — a saved-screen list has no reorder in this pass's
spec (load/rename/delete only).

`conditions`/`gate_overrides` are persisted as opaque JSON — this store never interprets their
shape, only round-trips it, matching exactly what `POST /analytics/screener/run`'s body already
carries, so saving/loading a screen is just "replay this same request body later."
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ScreenerScreenRow

logger = structlog.get_logger(__name__)


class ScreenerScreenNotFoundError(LookupError):
    pass


class ScreenerScreenNameConflictError(RuntimeError):
    """Raised when saving under a name that already exists for this `(user, market)` — the
    caller maps this to a 409 so the client can offer "另存新篩選" (README.md §6) rather than
    silently overwriting a different saved screen."""


class ScreenerScreen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    user_id: str
    name: str
    market: str
    conditions: tuple[dict[str, object], ...] = Field(default_factory=tuple)
    match: str = "AND"
    gate_overrides: dict[str, object] | None = None
    created_at: datetime
    updated_at: datetime


def _row_to_model(row: ScreenerScreenRow) -> ScreenerScreen:
    return ScreenerScreen(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        market=row.market,
        conditions=tuple(json.loads(row.conditions_json)),
        match=row.match,
        gate_overrides=(
            json.loads(row.gate_overrides_json) if row.gate_overrides_json is not None else None
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class ScreenerScreenStore:
    """SQLAlchemy-backed saved-screen store. The caller owns the session/transaction boundary,
    same convention as `WatchlistStore`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_screen(  # noqa: PLR0913 — one keyword-only field per persisted screen column
        self,
        *,
        user_id: str,
        name: str,
        market: str,
        conditions: tuple[dict[str, object], ...],
        match: str = "AND",
        gate_overrides: dict[str, object] | None = None,
    ) -> ScreenerScreen:
        """Create a new saved screen. Raises `ScreenerScreenNameConflictError` if `name` is
        already taken for this `(user, market)` — the caller (route) maps this to 409 rather
        than silently overwriting; renaming/updating an existing screen is `update_screen`, a
        distinct, explicit operation."""
        existing = await self._session.execute(
            select(ScreenerScreenRow.id).where(
                ScreenerScreenRow.user_id == user_id,
                ScreenerScreenRow.market == market,
                ScreenerScreenRow.name == name,
            )
        )
        if existing.first() is not None:
            raise ScreenerScreenNameConflictError(name)

        now = datetime.now(UTC)
        row = ScreenerScreenRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            name=name,
            market=market,
            conditions_json=json.dumps(list(conditions)),
            match=match,
            gate_overrides_json=(
                json.dumps(gate_overrides) if gate_overrides is not None else None
            ),
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("screener_screen_saved", user_id=user_id, market=market, name=name)
        return _row_to_model(row)

    async def list_screens(self, *, user_id: str, market: str) -> list[ScreenerScreen]:
        result = await self._session.execute(
            select(ScreenerScreenRow)
            .where(ScreenerScreenRow.user_id == user_id, ScreenerScreenRow.market == market)
            .order_by(ScreenerScreenRow.updated_at.desc())
        )
        return [_row_to_model(row) for row in result.scalars().all()]

    async def _screen_row(self, *, user_id: str, screen_id: str) -> ScreenerScreenRow:
        result = await self._session.execute(
            select(ScreenerScreenRow).where(
                ScreenerScreenRow.id == screen_id, ScreenerScreenRow.user_id == user_id
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ScreenerScreenNotFoundError(screen_id)
        return row

    async def update_screen(  # noqa: PLR0913 — one keyword-only field per updatable column
        self,
        *,
        user_id: str,
        screen_id: str,
        name: str | None = None,
        conditions: tuple[dict[str, object], ...] | None = None,
        match: str | None = None,
        gate_overrides: dict[str, object] | None = None,
    ) -> ScreenerScreen:
        """Rename and/or replace a saved screen's condition set in place — the "dirty ->
        update" path README.md §6 describes, distinct from `save_screen` (which always creates
        a new row)."""
        row = await self._screen_row(user_id=user_id, screen_id=screen_id)
        if name is not None:
            row.name = name
        if conditions is not None:
            row.conditions_json = json.dumps(list(conditions))
        if match is not None:
            row.match = match
        if gate_overrides is not None:
            row.gate_overrides_json = json.dumps(gate_overrides)
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        logger.info("screener_screen_updated", user_id=user_id, screen_id=screen_id)
        return _row_to_model(row)

    async def delete_screen(self, *, user_id: str, screen_id: str) -> None:
        """Idempotent delete: deleting a screen that isn't there (already deleted, or never the
        caller's) is a no-op, never an error — matches `WatchlistStore.remove_item`'s own
        idempotent-delete convention."""
        result = await self._session.execute(
            select(ScreenerScreenRow).where(
                ScreenerScreenRow.id == screen_id, ScreenerScreenRow.user_id == user_id
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()
            logger.info("screener_screen_deleted", user_id=user_id, screen_id=screen_id)


__all__ = [
    "ScreenerScreen",
    "ScreenerScreenNameConflictError",
    "ScreenerScreenNotFoundError",
    "ScreenerScreenStore",
]
