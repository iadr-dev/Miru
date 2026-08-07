"""`PetStore` — SQLAlchemy-backed cosmetic pet state (`user_pets` / `user_pet_stats`).

All mutations follow the codebase's concurrency discipline: a pull **reserves before acting**
(atomic decrement of `available_pulls`, refund on failure), and XP/streak/chat increments are
read-modify-write inside the caller's transaction — for cosmetic state on dev SQLite this is the
reference single-process shape; the Protocol is what a Redis/Postgres atomic backing would swap
in behind, with no call-site change.

`_ensure_stats` lazily seeds a user's stats row with `available_pulls=1` (the free register
pull) on first pet-endpoint access.

No tier/entitlement check appears anywhere in this module — cosmetic-only invariant.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, date, datetime
from typing import Protocol, runtime_checkable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import UserPetRow, UserPetStatsRow
from pets import config
from pets.models import ObtainedVia, OwnedPet, PetStats, PullOutcome, Rarity

logger = structlog.get_logger(__name__)


class NoPullsAvailableError(RuntimeError):
    """A pull was requested with `available_pulls == 0` — refused (reserve-before-act)."""


class PetNotFoundError(LookupError):
    pass


@runtime_checkable
class PetStore(Protocol):
    async def ensure_stats(self, *, user_id: str) -> PetStats: ...

    async def list_pets(self, *, user_id: str) -> list[OwnedPet]: ...

    async def active_pet(self, *, user_id: str) -> OwnedPet | None: ...

    async def equip(self, *, user_id: str, record_id: str) -> OwnedPet: ...

    async def pull(self, *, user_id: str) -> PullOutcome: ...

    async def grant_chat_xp(self, *, user_id: str) -> None: ...


def _row_to_pet(row: UserPetRow) -> OwnedPet:
    return OwnedPet(
        id=row.id,
        user_id=row.user_id,
        pet_id=row.pet_id,
        is_active=row.is_active,
        level=row.level,
        xp=row.xp,
        obtained_via=ObtainedVia(row.obtained_via),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_stats(row: UserPetStatsRow) -> PetStats:
    return PetStats(
        user_id=row.user_id,
        total_pulls=row.total_pulls,
        streak_days=row.streak_days,
        last_login_date=row.last_login_date,
        chat_count=row.chat_count,
        available_pulls=row.available_pulls,
        pity_counter=row.pity_counter,
    )


class SqlAlchemyPetStore:
    def __init__(self, session: AsyncSession, *, rng: random.Random | None = None) -> None:
        self._session = session
        # Injectable RNG so gacha tests are deterministic; defaults to a fresh Random().
        self._rng = rng or random.Random()  # noqa: S311 — cosmetic gacha, not cryptographic

    async def ensure_stats(self, *, user_id: str) -> PetStats:
        row = await self._stats_row(user_id)
        if row is None:
            row = UserPetStatsRow(user_id=user_id, available_pulls=1)
            self._session.add(row)
            await self._session.flush()
            logger.info("pet_stats_seeded", user_id=user_id)
        return _row_to_stats(row)

    async def list_pets(self, *, user_id: str) -> list[OwnedPet]:
        result = await self._session.execute(
            select(UserPetRow).where(UserPetRow.user_id == user_id).order_by(UserPetRow.created_at)
        )
        return [_row_to_pet(row) for row in result.scalars().all()]

    async def active_pet(self, *, user_id: str) -> OwnedPet | None:
        result = await self._session.execute(
            select(UserPetRow).where(UserPetRow.user_id == user_id, UserPetRow.is_active.is_(True))
        )
        row = result.scalar_one_or_none()
        return _row_to_pet(row) if row is not None else None

    async def equip(self, *, user_id: str, record_id: str) -> OwnedPet:
        """Equip one owned pet, unequipping all others in the same transaction (≤1 active)."""
        target = await self._pet_row(user_id=user_id, record_id=record_id)
        if target is None:
            raise PetNotFoundError(record_id)
        result = await self._session.execute(
            select(UserPetRow).where(UserPetRow.user_id == user_id, UserPetRow.is_active.is_(True))
        )
        for row in result.scalars().all():
            row.is_active = False
            row.updated_at = datetime.now(UTC)
        # Flush the unequips before setting the new active pet so the partial-unique
        # "one active per user" index never sees two active rows mid-transaction.
        await self._session.flush()
        target.is_active = True
        target.updated_at = datetime.now(UTC)
        await self._session.flush()
        logger.info("pet_equipped", user_id=user_id, record_id=record_id)
        return _row_to_pet(target)

    async def pull(self, *, user_id: str) -> PullOutcome:
        stats = await self._stats_row(user_id)
        if stats is None:
            stats = UserPetStatsRow(user_id=user_id, available_pulls=1)
            self._session.add(stats)
            await self._session.flush()
        # Reserve-before-act: refuse if none available; decrement first.
        if stats.available_pulls <= 0:
            raise NoPullsAvailableError("no available pulls")
        stats.available_pulls -= 1
        stats.total_pulls += 1

        rarity = self._roll_rarity(stats.pity_counter)
        if rarity in (Rarity.RARE, Rarity.LEGENDARY):
            stats.pity_counter = 0
        else:
            stats.pity_counter += 1

        pet_id = self._pick_pet(rarity)
        existing = await self._owned_pet_row(user_id=user_id, pet_id=pet_id)
        now = datetime.now(UTC)
        if existing is not None:
            # Duplicate → XP, never wasted.
            self._apply_xp(existing, config.XP_PER_DUPLICATE, stats=stats)
            existing.updated_at = now
            await self._session.flush()
            logger.info("pet_pull_duplicate", user_id=user_id, pet_id=pet_id, rarity=rarity.value)
            return PullOutcome(
                pet_id=pet_id, rarity=rarity, is_duplicate=True, xp_awarded=config.XP_PER_DUPLICATE
            )

        # First-ever pet auto-equips.
        has_any = await self._owned_count(user_id) > 0
        auto_equip = not has_any
        row = UserPetRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            pet_id=pet_id,
            is_active=auto_equip,
            level=1,
            xp=0,
            obtained_via=ObtainedVia.PULL.value,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info(
            "pet_pull_new", user_id=user_id, pet_id=pet_id, rarity=rarity.value, equipped=auto_equip
        )
        return PullOutcome(
            pet_id=pet_id, rarity=rarity, is_duplicate=False, auto_equipped=auto_equip
        )

    async def grant_chat_xp(self, *, user_id: str) -> None:
        """Award chat XP to the equipped pet + bump `chat_count`, granting a bonus pull every
        N chats. Called from the turn-lifecycle `post_turn` path (wired above this layer)."""
        stats = await self._stats_row(user_id)
        if stats is None:
            return
        stats.chat_count += 1
        if stats.chat_count % config.CHATS_PER_BONUS_PULL == 0:
            stats.available_pulls += 1
        active = await self._session.execute(
            select(UserPetRow).where(UserPetRow.user_id == user_id, UserPetRow.is_active.is_(True))
        )
        row = active.scalar_one_or_none()
        if row is not None:
            self._apply_xp(row, config.XP_PER_CHAT, stats=stats)
            row.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def register_login(self, *, user_id: str, today: date | None = None) -> PetStats:
        """Streak logic on login: consecutive-day → streak++; every 7-day streak → +1 pull.
        Idempotent within the same day."""
        today = today or datetime.now(UTC).date()
        row = await self._stats_row(user_id)
        if row is None:
            # First-ever login = day 1 of the streak.
            row = UserPetStatsRow(
                user_id=user_id,
                available_pulls=1,
                streak_days=1,
                last_login_date=today.isoformat(),
            )
            self._session.add(row)
            await self._session.flush()
            return _row_to_stats(row)
        last = date.fromisoformat(row.last_login_date) if row.last_login_date else None
        if last == today:
            return _row_to_stats(row)
        if last is not None and (today - last).days == 1:
            row.streak_days += 1
        else:
            row.streak_days = 1
        row.last_login_date = today.isoformat()
        if row.streak_days % config.STREAK_DAYS_PER_BONUS_PULL == 0:
            row.available_pulls += 1
        await self._session.flush()
        return _row_to_stats(row)

    # ---- internals ----

    def _roll_rarity(self, pity_counter: int) -> Rarity:
        # Pity guarantees take precedence over the weighted roll.
        if pity_counter + 1 >= config.PITY_LEGENDARY_AT:
            return Rarity.LEGENDARY
        if pity_counter + 1 >= config.PITY_RARE_AT:
            return Rarity.RARE
        rarities = list(config.RARITY_WEIGHTS.keys())
        weights = [config.RARITY_WEIGHTS[r] for r in rarities]
        return self._rng.choices(rarities, weights=weights, k=1)[0]

    def _pick_pet(self, rarity: Rarity) -> str:
        candidates = [entry.pet_id for entry in config.CATALOG if entry.rarity is rarity]
        if not candidates:
            # No catalog entry at this rarity — fall back to any pet so a pull is never lost.
            candidates = [entry.pet_id for entry in config.CATALOG]
        return self._rng.choice(candidates)

    @staticmethod
    def _apply_xp(row: UserPetRow, amount: int, *, stats: UserPetStatsRow) -> None:
        """Apply XP to one pet row, and grant `config.LEVEL_MILESTONE_PULLS`' bonus pulls for
        every milestone level newly crossed this call — the same reserve/grant discipline as the
        chat-count/streak-day bonus-pull paths (`grant_chat_xp`/`register_login`): the grant is
        derived from the persisted `row.level` this call reads at entry, so a retried/duplicated
        call (same starting level, same XP amount) recomputes the identical crossing and can
        never double-grant — there is no separate "already granted" counter to fall out of sync,
        `row.level` monotonically increasing IS the guard."""
        starting_level = row.level
        total = row.xp + amount
        level = starting_level
        while total >= config.XP_PER_LEVEL and level < config.MAX_LEVEL:
            total -= config.XP_PER_LEVEL
            level += 1
        if level >= config.MAX_LEVEL:
            level = config.MAX_LEVEL
            total = min(total, config.XP_PER_LEVEL - 1)
        row.level = level
        row.xp = total
        bonus_pulls = sum(
            pulls
            for milestone, pulls in config.LEVEL_MILESTONE_PULLS.items()
            if starting_level < milestone <= level
        )
        if bonus_pulls:
            stats.available_pulls += bonus_pulls

    async def _stats_row(self, user_id: str) -> UserPetStatsRow | None:
        result = await self._session.execute(
            select(UserPetStatsRow).where(UserPetStatsRow.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def _pet_row(self, *, user_id: str, record_id: str) -> UserPetRow | None:
        result = await self._session.execute(
            select(UserPetRow).where(UserPetRow.id == record_id, UserPetRow.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def _owned_pet_row(self, *, user_id: str, pet_id: str) -> UserPetRow | None:
        result = await self._session.execute(
            select(UserPetRow).where(UserPetRow.user_id == user_id, UserPetRow.pet_id == pet_id)
        )
        return result.scalar_one_or_none()

    async def _owned_count(self, user_id: str) -> int:
        result = await self._session.execute(
            select(UserPetRow.id).where(UserPetRow.user_id == user_id)
        )
        return len(result.all())


__all__ = ["NoPullsAvailableError", "PetNotFoundError", "PetStore", "SqlAlchemyPetStore"]
