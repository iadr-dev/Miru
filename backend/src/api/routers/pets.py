"""Pets & gamification REST — cosmetic, no tier gate anywhere (the cosmetic-only invariant the
`pets.store` module also enforces by never consulting entitlement).

Every route is user-scoped through `PetStore` (methods take `user_id`). The catalog is
code-defined config, so `/pets/catalog` needs no store call. Progress is assembled from the
stats row plus the code-defined milestone/completion constants.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import User
from api.dependencies import get_session, require_user
from api.errors import ApiError
from pets import config
from pets.models import OwnedPet, PetCatalogEntry, PetStats, PullOutcome
from pets.store import (
    NoPullsAvailableError,
    PetNotFoundError,
    SqlAlchemyPetStore,
)

router = APIRouter(tags=["pets"])


def _store(session: AsyncSession = Depends(get_session)) -> SqlAlchemyPetStore:
    return SqlAlchemyPetStore(session)


@router.get("/pets/catalog")
async def catalog() -> dict[str, tuple[PetCatalogEntry, ...]]:
    """The full code-defined catalog (rarity, bilingual names, descriptions) — no auth needed
    to browse, but kept behind the same router group for locality."""
    return {"catalog": config.CATALOG}


@router.get("/pets")
async def my_pets(
    user: User = Depends(require_user),
    store: SqlAlchemyPetStore = Depends(_store),
) -> dict[str, list[OwnedPet]]:
    return {"pets": await store.list_pets(user_id=user.id)}


@router.get("/pets/active")
async def active_pet(
    user: User = Depends(require_user),
    store: SqlAlchemyPetStore = Depends(_store),
) -> dict[str, OwnedPet | None]:
    return {"active": await store.active_pet(user_id=user.id)}


@router.put("/pets/{record_id}/equip")
async def equip(
    record_id: str,
    user: User = Depends(require_user),
    store: SqlAlchemyPetStore = Depends(_store),
) -> OwnedPet:
    try:
        return await store.equip(user_id=user.id, record_id=record_id)
    except PetNotFoundError as exc:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="pet_not_found",
            message=f"Pet {record_id} not found.",
            retryable=False,
        ) from exc


class PetProgress(PetStats):
    """The stats row plus the code-defined completion/milestone/pity context the UI renders.

    `pity_rare_at`/`pity_legendary_at`/`level_milestones` are the raw configured thresholds
    `pets.config` already defines (`PITY_RARE_AT`/`PITY_LEGENDARY_AT`/`LEVEL_MILESTONE_PULLS`) —
    reporting them here is what lets a client show "N pulls to guaranteed rare" or "next
    milestone at level M" against the stats this same response already carries (`pity_counter`
    for the pity math; the *equipped* pet's own `level`, from `GET /pets/active`, for the
    milestone math) without hardcoding those thresholds client-side."""

    catalog_size: int
    owned_count: int
    chats_per_bonus_pull: int
    streak_days_per_bonus_pull: int
    pity_rare_at: int
    pity_legendary_at: int
    level_milestones: dict[int, int]


@router.get("/pets/progress")
async def progress(
    user: User = Depends(require_user),
    store: SqlAlchemyPetStore = Depends(_store),
) -> PetProgress:
    stats = await store.ensure_stats(user_id=user.id)
    owned = await store.list_pets(user_id=user.id)
    return PetProgress(
        **stats.model_dump(),
        catalog_size=config.catalog_size(),
        owned_count=len(owned),
        chats_per_bonus_pull=config.CHATS_PER_BONUS_PULL,
        streak_days_per_bonus_pull=config.STREAK_DAYS_PER_BONUS_PULL,
        pity_rare_at=config.PITY_RARE_AT,
        pity_legendary_at=config.PITY_LEGENDARY_AT,
        level_milestones=dict(config.LEVEL_MILESTONE_PULLS),
    )


@router.post("/pets/pull", status_code=status.HTTP_201_CREATED)
async def pull(
    user: User = Depends(require_user),
    store: SqlAlchemyPetStore = Depends(_store),
) -> PullOutcome:
    """Gacha pull (reserve-before-act on `available_pulls` in the store). No pulls left → 409."""
    try:
        return await store.pull(user_id=user.id)
    except NoPullsAvailableError as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="no_pulls_available",
            message="No pulls available — earn more by chatting or maintaining your streak.",
            retryable=False,
        ) from exc


__all__ = ["router"]
