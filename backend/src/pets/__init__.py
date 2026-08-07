"""Pet & gamification — a cosmetic companion + engagement layer.

Explicitly outside the analysis + entitlement path: pets grant no data access, no quota, no
analytical capability; rarity/level affect only cosmetic tone via `persona.pet_hint_for`
(the [4] `PromptLayers.pet_hint` layer). Never tier-gated, never part of `data_class`, never
billed. This package must never import `accounts`/`voice`/`payments` (it is an interaction-layer
package below them).
"""

from __future__ import annotations

from pets.models import OwnedPet, PetCatalogEntry, PetStats, PullOutcome, Rarity
from pets.persona import pet_hint_for

__all__ = [
    "OwnedPet",
    "PetCatalogEntry",
    "PetStats",
    "PullOutcome",
    "Rarity",
    "pet_hint_for",
]
