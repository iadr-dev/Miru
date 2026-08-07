"""The pet persona hint — the [4] `PromptLayers.pet_hint` layer.

This is the *only* way a pet touches the agent loop: a small rarity/level-scaled tone nudge
appended to the system prompt **below** the governance prompt AND the agent personality ([2]).
It changes how warmly the assistant phrases a closing line — never what data, score, or caveat
it produces. Because it is the lowest styling layer and the final answer is always run through
`agent.guardrails.check_output`, a pet hint can never introduce trading language or drop a
caveat (proven by test).

Critically, this module has **no** import of `accounts`/entitlements/tools — pets sit entirely
outside the analysis + entitlement path, which is this package's single most important
invariant. It only maps an equipped pet's cosmetic state → a short prompt string.
"""

from __future__ import annotations

from pets.config import CATALOG_BY_ID
from pets.models import OwnedPet, Rarity

# Rarity → base warmth of the companion nudge. Purely stylistic.
_RARITY_FLAVOR: dict[Rarity, str] = {
    Rarity.COMMON: "以一句輕鬆、鼓勵的結語收尾即可。",
    Rarity.UNCOMMON: "可用稍微生動一點的語氣，結語親切。",
    Rarity.RARE: "語氣可再溫暖、細緻一些，像一位熟悉的夥伴。",
    Rarity.LEGENDARY: "語氣溫暖而有陪伴感，像一位長期同行的好夥伴。",
}

_HIGH_LEVEL_THRESHOLD = 10


def pet_hint_for(pet: OwnedPet | None) -> str | None:
    """The `PromptLayers.pet_hint` string for the user's equipped pet, or `None` when no pet is
    equipped (the pet layer is simply absent — a user with the pet disabled still has a
    personality). The hint is framed explicitly as tone-only and non-advisory."""
    if pet is None:
        return None
    entry = CATALOG_BY_ID.get(pet.pet_id)
    if entry is None:
        return None
    flavor = _RARITY_FLAVOR[entry.rarity]
    warmth = "，語氣可更熟稔友善" if pet.level >= _HIGH_LEVEL_THRESHOLD else ""
    return (
        f"（陪伴提示：使用者的夥伴是「{entry.name_zh}」{warmth}。{flavor}"
        f"這只影響語氣的溫度，不改變任何數據、分析、評分或必要的免責聲明；"
        f"你仍是分析助理，夥伴不會給出任何投資建議。）"
    )


__all__ = ["pet_hint_for"]
