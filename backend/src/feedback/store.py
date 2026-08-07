"""`FeedbackStore` — the minimal 👍/👎 (+ optional short comment) rating capture on a turn.

Deliberately narrow, per this build's scope: a real DB row per `(turn_id, user_id)`, upserted on
re-submit, and nothing else. There is no rolling quality score, no alert threshold, no admin
`quality`/`alerts`/`recent` endpoint here — that pipeline is a dedicated follow-up this build does
not own (see `feedback/provenance.py`'s own scope note for the same boundary on the skill-
attribution side).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import structlog
from sqlalchemy import Insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import TurnFeedbackRow

logger = structlog.get_logger(__name__)

_RATINGS = ("up", "down")


@dataclass(frozen=True)
class FeedbackRecord:
    """The persisted outcome of a feedback submission — enough for the route to echo back."""

    turn_id: str
    user_id: str
    rating: str
    comment: str | None


@runtime_checkable
class FeedbackStore(Protocol):
    async def submit(
        self, *, turn_id: str, user_id: str, rating: str, comment: str | None
    ) -> FeedbackRecord: ...


class SqlAlchemyFeedbackStore:
    """`FeedbackStore` over `turn_feedback`, using the caller's own per-request session (same
    per-request-session convention as `conversation.store.SqlAlchemyConversationStore`)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def submit(
        self, *, turn_id: str, user_id: str, rating: str, comment: str | None
    ) -> FeedbackRecord:
        """Upsert on `(turn_id, user_id)` — a re-submit (e.g. thumbs-down then thumbs-up) updates
        the one row in place rather than accumulating duplicates, in the same atomic-upsert style
        as `usage.store.write_turn_usage`."""
        if rating not in _RATINGS:
            raise ValueError(f"rating must be one of {_RATINGS}, got {rating!r}")
        now = datetime.now(UTC)
        values = {
            "id": str(uuid.uuid4()),
            "turn_id": turn_id,
            "user_id": user_id,
            "rating": rating,
            "comment": comment,
            "created_at": now,
            "updated_at": now,
        }
        conflict = ["turn_id", "user_id"]
        update_cols = {"rating": rating, "comment": comment, "updated_at": now}
        if self._session.get_bind().dialect.name == "postgresql":
            pstmt = pg_insert(TurnFeedbackRow).values(**values)
            stmt: Insert = pstmt.on_conflict_do_update(index_elements=conflict, set_=update_cols)
        else:
            sstmt = sqlite_insert(TurnFeedbackRow).values(**values)
            stmt = sstmt.on_conflict_do_update(index_elements=conflict, set_=update_cols)
        await self._session.execute(stmt)
        await self._session.flush()
        logger.info("turn_feedback_submitted", turn_id=turn_id, user_id=user_id, rating=rating)
        return FeedbackRecord(turn_id=turn_id, user_id=user_id, rating=rating, comment=comment)


__all__ = ["FeedbackRecord", "FeedbackStore", "SqlAlchemyFeedbackStore"]
