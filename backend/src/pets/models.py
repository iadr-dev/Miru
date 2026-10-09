"""Pet domain models. Cosmetic-only — no combat stats, no data/quota/entitlement linkage.
Per-owned-pet state is just `level` + `xp`; rarity is a drop-weight + companion-persona tier.
The catalog is code-defined config (`pets.catalog`), not a DB table.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Rarity(StrEnum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    LEGENDARY = "legendary"


class ObtainedVia(StrEnum):
    PULL = "pull"
    ADMIN = "admin"


class PetCatalogEntry(BaseModel):
    """One catalog pet definition. Bilingual, TW/finance flavor lives only in name/description
    — never in mechanics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pet_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    name_zh: str = Field(min_length=1)
    rarity: Rarity
    description: str = Field(min_length=1)
    description_zh: str = Field(min_length=1)


class OwnedPet(BaseModel):
    """One `user_pets` row — an owned, possibly-equipped pet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    user_id: str
    pet_id: str
    is_active: bool
    level: int
    xp: int
    obtained_via: ObtainedVia
    created_at: datetime
    updated_at: datetime


class PetStats(BaseModel):
    """One `user_pet_stats` row — gacha/engagement counters for a user."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    total_pulls: int
    streak_days: int
    last_login_date: str | None
    chat_count: int
    available_pulls: int
    pity_counter: int
    hint_enabled: bool = False
    companion_visible: bool = True


class PullOutcome(BaseModel):
    """The result of a gacha pull — either a newly obtained pet or a duplicate converted to XP."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pet_id: str
    rarity: Rarity
    is_duplicate: bool
    xp_awarded: int = 0
    auto_equipped: bool = False


class ChatReward(BaseModel):
    """What one completed chat earned the equipped pet (all zero past the daily cap)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    xp: int = 0
    pet_id: str | None = None
    leveled_to: int | None = None
    pulls_earned: int = 0


__all__ = [
    "ChatReward",
    "ObtainedVia",
    "OwnedPet",
    "PetCatalogEntry",
    "PetStats",
    "PullOutcome",
    "Rarity",
]
