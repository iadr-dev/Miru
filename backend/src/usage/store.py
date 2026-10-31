"""Durable usage accounting: the write-through sink for finished-turn rollups and the
authoritative reserve-before-act counter behind daily-chat-limit enforcement.

Two concerns, both flush-safe in the app database (never Redis) because they gate a hard cap and
must survive an ephemeral-cache wipe:

- `reserve_daily_chat` — the pre-turn gate. It increments the caller's UTC-day chat counter and
  the *same atomic upsert* returns the post-increment total, so the "would this exceed the cap?"
  decision and the increment are one operation: two concurrent turns for the same user can never
  both read `n = limit - 1` and both proceed. Over-cap callers are refunded (`release`) so a
  rejected turn does not burn the day's allowance.
- `record_turn` — the post-turn write-through of a completed turn's usage snapshot. Idempotent on
  `(user_id, turn_id)`: a retried write collides and is a no-op rather than a double-count.

The atomic upsert is expressed dialect-aware (`INSERT … ON CONFLICT DO UPDATE SET n = n + :delta`
on both SQLite and Postgres) — the same `ON CONFLICT` discipline the rest of the codebase's
counters use, never a read-then-write.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, Protocol, cast, runtime_checkable

import structlog
from sqlalchemy import CursorResult, Insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import UsageCounterRow, UsageEventRow

logger = structlog.get_logger(__name__)

_CHAT_COUNTER = "chat"
_VOICE_COUNTERS = {"stt": "voice_stt", "tts": "voice_tts"}
VoiceKind = Literal["stt", "tts"]


@dataclass(frozen=True)
class ChatQuotaVerdict:
    """The outcome of a pre-turn daily-chat reservation. `allowed` is False only when a finite cap
    would be exceeded; `used`/`limit` describe the post-reservation position for messaging."""

    allowed: bool
    used: int
    limit: int | None


def _day_key(when: date) -> str:
    return when.isoformat()


async def write_turn_usage(  # noqa: PLR0913 — one field per usage dimension the snapshot carries
    session: AsyncSession,
    *,
    user_id: str,
    turn_id: str,
    input_tokens: int,
    output_tokens: int,
    tool_calls: int,
    finmind_calls: int,
    cost_micros: int,
) -> bool:
    """Insert one durable per-turn rollup on the *caller's* session (no commit — the caller's
    unit-of-work owns that), idempotent on `(user_id, turn_id)`. Sharing the request's session is
    what keeps the post-turn write on one connection rather than a second writer contending for
    the same rows. Returns whether a new row landed."""
    values = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "turn_id": turn_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tool_calls": tool_calls,
        "finmind_calls": finmind_calls,
        "cost_micros": cost_micros,
        "created_at": datetime.now(UTC),
    }
    conflict = ["user_id", "turn_id"]
    if session.get_bind().dialect.name == "postgresql":
        pstmt = pg_insert(UsageEventRow).values(**values)
        stmt: Insert = pstmt.on_conflict_do_nothing(index_elements=conflict)
    else:
        sstmt = sqlite_insert(UsageEventRow).values(**values)
        stmt = sstmt.on_conflict_do_nothing(index_elements=conflict)
    result = cast("CursorResult[object]", await session.execute(stmt))
    return bool(result.rowcount)


@runtime_checkable
class UsageStore(Protocol):
    async def reserve_daily_chat(
        self, *, user_id: str, limit: int | None, today: date | None = None
    ) -> ChatQuotaVerdict: ...

    async def refund_daily_chat(
        self, *, user_id: str, limit: int | None, today: date | None = None
    ) -> None: ...

    async def today_counts(self, *, user_id: str, today: date | None = None) -> dict[str, int]: ...

    async def reserve_daily_voice(
        self, *, user_id: str, kind: VoiceKind, limit: int, today: date | None = None
    ) -> ChatQuotaVerdict: ...

    async def refund_daily_voice(
        self, *, user_id: str, kind: VoiceKind, today: date | None = None
    ) -> None: ...

    async def record_turn(  # noqa: PLR0913 — one field per usage dimension the snapshot carries
        self,
        *,
        user_id: str,
        turn_id: str,
        input_tokens: int,
        output_tokens: int,
        tool_calls: int,
        finmind_calls: int,
        cost_micros: int,
    ) -> bool: ...


class ChatClaim:
    """One chat reserved from a user's daily allowance for a turn that has not started yet.

    The caller wraps every step between the reservation and the turn start in
    `async with claim.refund_unless_started(session):` and calls `turn_started()` once the turn
    runs. Leaving the block before that (an error, an early return) gives the unit back once, to
    the UTC day it was claimed on. A started turn keeps its unit, whatever happens to it later."""

    def __init__(
        self, store: UsageStore | None, *, user_id: str, limit: int | None, day: date
    ) -> None:
        self._store = store
        self._user_id = user_id
        self._limit = limit
        self._day = day
        self._settled = store is None or limit is None

    @classmethod
    def unmetered(cls) -> ChatClaim:
        """A caller with no daily cap (Pro, their own key): nothing was reserved."""
        return cls(None, user_id="", limit=None, day=date.min)

    def turn_started(self) -> None:
        self._settled = True

    @contextlib.asynccontextmanager
    async def refund_unless_started(self, session: AsyncSession) -> AsyncGenerator[None]:
        """`session` is the caller's own, rolled back before the refund: on SQLite a write it still
        holds would block the refund's separate connection."""
        try:
            yield
        finally:
            await self._refund(session)

    async def _refund(self, session: AsyncSession) -> None:
        if self._settled or self._store is None:
            return
        self._settled = True
        try:
            await session.rollback()
            await self._store.refund_daily_chat(
                user_id=self._user_id, limit=self._limit, today=self._day
            )
        except Exception:  # the request's own error is the one to report, not the refund's
            logger.exception("chat_refund_failed", user_id=self._user_id)


async def claim_daily_chat(
    store: UsageStore, *, user_id: str, limit: int | None, today: date | None = None
) -> ChatClaim | None:
    """Reserve one chat from the caller's allowance for `today` (default: the UTC date now), or
    None when the cap is reached."""
    if limit is None:
        return ChatClaim.unmetered()
    day = today or datetime.now(UTC).date()
    verdict = await store.reserve_daily_chat(user_id=user_id, limit=limit, today=day)
    if not verdict.allowed:
        return None
    return ChatClaim(store, user_id=user_id, limit=limit, day=day)


class SqlAlchemyUsageStore:
    """`UsageStore` over `usage_counters`/`usage_events`. Each call opens its own short session
    from the injected factory, so one shared store is safe across the concurrent turns a
    multi-worker deployment runs — no session is held between calls."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def _increment(
        self, session: AsyncSession, *, user_id: str, window_key: str, counter: str
    ) -> int:
        now = datetime.now(UTC)
        base = {
            "user_id": user_id,
            "counter": counter,
            "window_key": window_key,
            "n": 1,
            "updated_at": now,
        }
        conflict = ["user_id", "counter", "window_key"]
        set_ = {"n": UsageCounterRow.__table__.c.n + 1, "updated_at": now}
        returning = UsageCounterRow.__table__.c.n
        if session.get_bind().dialect.name == "postgresql":
            stmt = (
                pg_insert(UsageCounterRow)
                .values(**base)
                .on_conflict_do_update(index_elements=conflict, set_=set_)
                .returning(returning)
            )
        else:
            stmt = (
                sqlite_insert(UsageCounterRow)
                .values(**base)
                .on_conflict_do_update(index_elements=conflict, set_=set_)
                .returning(returning)
            )
        result = await session.execute(stmt)
        return int(result.scalar_one())

    async def reserve_daily_chat(
        self, *, user_id: str, limit: int | None, today: date | None = None
    ) -> ChatQuotaVerdict:
        """Atomically claim one chat against the caller's UTC-day allowance. `limit is None` means
        unlimited (Pro / BYOK) — never gated, and no counter row is touched. A finite cap
        increments-and-checks in one upsert; if the post-increment total exceeds the cap the claim
        is refunded and refused, so a rejected turn does not consume the day's quota."""
        if limit is None:
            return ChatQuotaVerdict(allowed=True, used=0, limit=None)
        return await self._reserve(_CHAT_COUNTER, user_id=user_id, limit=limit, today=today)

    async def refund_daily_chat(
        self, *, user_id: str, limit: int | None, today: date | None = None
    ) -> None:
        """Give back a chat claimed by `reserve_daily_chat` for a turn that was not started
        (`ChatClaim`). An unlimited caller never claimed one."""
        if limit is None:
            return
        await self._refund(_CHAT_COUNTER, user_id=user_id, today=today)

    async def reserve_daily_voice(
        self, *, user_id: str, kind: VoiceKind, limit: int, today: date | None = None
    ) -> ChatQuotaVerdict:
        """The same reserve-before-act claim for voice requests (separate STT / TTS counters)."""
        return await self._reserve(_VOICE_COUNTERS[kind], user_id=user_id, limit=limit, today=today)

    async def refund_daily_voice(
        self, *, user_id: str, kind: VoiceKind, today: date | None = None
    ) -> None:
        await self._refund(_VOICE_COUNTERS[kind], user_id=user_id, today=today)

    async def _refund(self, counter: str, *, user_id: str, today: date | None) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(UsageCounterRow)
                .where(
                    UsageCounterRow.user_id == user_id,
                    UsageCounterRow.counter == counter,
                    UsageCounterRow.window_key == _day_key(today or datetime.now(UTC).date()),
                    UsageCounterRow.n > 0,
                )
                .values(n=UsageCounterRow.n - 1)
            )
            await session.commit()

    async def _reserve(
        self, counter: str, *, user_id: str, limit: int, today: date | None
    ) -> ChatQuotaVerdict:
        window_key = _day_key(today or datetime.now(UTC).date())
        async with self._session_factory() as session:
            used = await self._increment(
                session, user_id=user_id, window_key=window_key, counter=counter
            )
            if used > limit:
                await session.execute(
                    update(UsageCounterRow)
                    .where(
                        UsageCounterRow.user_id == user_id,
                        UsageCounterRow.counter == counter,
                        UsageCounterRow.window_key == window_key,
                    )
                    .values(n=UsageCounterRow.n - 1)
                )
                await session.commit()
                return ChatQuotaVerdict(allowed=False, used=limit, limit=limit)
            await session.commit()
            return ChatQuotaVerdict(allowed=True, used=used, limit=limit)

    async def today_counts(self, *, user_id: str, today: date | None = None) -> dict[str, int]:
        """How much of each daily allowance the caller has claimed so far (UTC day)."""
        window_key = _day_key(today or datetime.now(UTC).date())
        async with self._session_factory() as session:
            rows = await session.execute(
                select(UsageCounterRow.counter, UsageCounterRow.n).where(
                    UsageCounterRow.user_id == user_id,
                    UsageCounterRow.window_key == window_key,
                )
            )
            counts = {counter: int(n) for counter, n in rows.all()}
        return {"chats": counts.get(_CHAT_COUNTER, 0)}

    async def record_turn(  # noqa: PLR0913 — one field per usage dimension the snapshot carries
        self,
        *,
        user_id: str,
        turn_id: str,
        input_tokens: int,
        output_tokens: int,
        tool_calls: int,
        finmind_calls: int,
        cost_micros: int,
    ) -> bool:
        """Write the durable per-turn rollup. Idempotent on `(user_id, turn_id)` via
        `ON CONFLICT DO NOTHING` — a retried post-turn write is a no-op, never a double-count.
        Returns whether a new row landed."""
        async with self._session_factory() as session:
            landed = await write_turn_usage(
                session,
                user_id=user_id,
                turn_id=turn_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tool_calls=tool_calls,
                finmind_calls=finmind_calls,
                cost_micros=cost_micros,
            )
            await session.commit()
            return landed


__all__ = [
    "ChatClaim",
    "ChatQuotaVerdict",
    "SqlAlchemyUsageStore",
    "UsageStore",
    "claim_daily_chat",
    "write_turn_usage",
]
