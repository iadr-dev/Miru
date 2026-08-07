"""Pet catalog + tuning config. Code-defined, not a DB table — our own catalog and mechanics
values (adjustable here without a schema change). Purely cosmetic: rarity is a drop weight + a
companion-persona tier, nothing else.
"""

from __future__ import annotations

from typing import Final

from pets.models import PetCatalogEntry, Rarity

# The catalog — 藍鵲/Sisi flagship + a wider 台灣特有種 line. Flavor lives only in
# names/descriptions. 15 pets across 4 rarities, weighted toward common/uncommon.
CATALOG: Final[tuple[PetCatalogEntry, ...]] = (
    # Legendary (2)
    PetCatalogEntry(
        pet_id="sisi_magpie",
        name="Sisi the Blue Magpie",
        name_zh="思思藍鵲",
        rarity=Rarity.LEGENDARY,
        description="The Taiwan Blue Magpie flagship companion — bright, loyal, watches the board.",
    ),
    PetCatalogEntry(
        pet_id="formosan_black_bear",
        name="Yue the Formosan Black Bear",
        name_zh="台灣黑熊小月",
        rarity=Rarity.LEGENDARY,
        description="A 台灣黑熊 (Formosan black bear) — the island's iconic apex companion.",
    ),
    # Rare (3)
    PetCatalogEntry(
        pet_id="leopard_cat",
        name="Shan the Leopard Cat",
        name_zh="石虎小珊",
        rarity=Rarity.RARE,
        description="A 石虎 (Taiwan leopard cat) — rare, quiet, and observant.",
    ),
    PetCatalogEntry(
        pet_id="spoonbill",
        name="Pi the Spoonbill",
        name_zh="黑面琵鷺阿琵",
        rarity=Rarity.RARE,
        description="A 黑面琵鷺 (black-faced spoonbill) — a graceful seasonal visitor.",
    ),
    PetCatalogEntry(
        pet_id="mikado_pheasant",
        name="Di the Mikado Pheasant",
        name_zh="帝雉小帝",
        rarity=Rarity.RARE,
        description="A 帝雉 (Mikado pheasant) — Taiwan's high-mountain 'king of pheasants.'",
    ),
    # Uncommon (4)
    PetCatalogEntry(
        pet_id="salmon",
        name="Gu the Masu Salmon",
        name_zh="櫻花鉤吻鮭小鉤",
        rarity=Rarity.UNCOMMON,
        description="A 櫻花鉤吻鮭 (Formosan landlocked salmon) — persistent, swims upstream.",
    ),
    PetCatalogEntry(
        pet_id="sika_deer",
        name="Lu the Sika Deer",
        name_zh="水鹿小鹿",
        rarity=Rarity.UNCOMMON,
        description="A 水鹿 (Formosan sika deer) — calm, sure-footed on mountain trails.",
    ),
    PetCatalogEntry(
        pet_id="formosan_macaque",
        name="Hou the Formosan Macaque",
        name_zh="台灣獼猴阿猴",
        rarity=Rarity.UNCOMMON,
        description="A 台灣獼猴 (Formosan rock macaque) — clever, social, and quick.",
    ),
    PetCatalogEntry(
        pet_id="formosan_serow",
        name="Rong the Formosan Serow",
        name_zh="台灣長鬃山羊小絨",
        rarity=Rarity.UNCOMMON,
        description="A 台灣長鬃山羊 (Formosan serow) — a sure-footed high-altitude climber.",
    ),
    # Common (6)
    PetCatalogEntry(
        pet_id="mountain_sparrow",
        name="Que the Sparrow",
        name_zh="麻雀小雀",
        rarity=Rarity.COMMON,
        description="A cheerful everyday sparrow companion.",
    ),
    PetCatalogEntry(
        pet_id="bamboo_partridge",
        name="Zhu the Partridge",
        name_zh="竹雞阿竹",
        rarity=Rarity.COMMON,
        description="A 竹雞 (Chinese bamboo partridge) — grounded and dependable.",
    ),
    PetCatalogEntry(
        pet_id="crested_serpent_eagle",
        name="Ying the Crested Serpent Eagle",
        name_zh="大冠鷲小鷲",
        rarity=Rarity.COMMON,
        description="A 大冠鷲 (crested serpent eagle) — sharp-eyed, circles high over the market.",
    ),
    PetCatalogEntry(
        pet_id="flying_squirrel",
        name="Shu the Flying Squirrel",
        name_zh="白面鼯鼠小鼯",
        rarity=Rarity.COMMON,
        description="A 白面鼯鼠 (white-faced flying squirrel) — a nimble nighttime glider.",
    ),
    PetCatalogEntry(
        pet_id="wild_boar",
        name="Zhu the Wild Boar",
        name_zh="台灣野豬小豬",
        rarity=Rarity.COMMON,
        description="A 台灣野豬 (Formosan wild boar) — stubborn, sturdy, and always foraging.",
    ),
    PetCatalogEntry(
        pet_id="pangolin",
        name="Ling the Pangolin",
        name_zh="穿山甲小鯪",
        rarity=Rarity.COMMON,
        description="A 穿山甲 (Formosan pangolin) — armored, patient, and endearingly shy.",
    ),
)

CATALOG_BY_ID: Final[dict[str, PetCatalogEntry]] = {entry.pet_id: entry for entry in CATALOG}

# Starting drop weights (≈ 50/30/15/5 common/uncommon/rare/legendary) — tunable here.
RARITY_WEIGHTS: Final[dict[Rarity, int]] = {
    Rarity.COMMON: 50,
    Rarity.UNCOMMON: 30,
    Rarity.RARE: 15,
    Rarity.LEGENDARY: 5,
}

# Pity thresholds (≈ 30 / 50) — a guaranteed rare at `RARE_AT`, legendary at `LEGENDARY_AT`.
# Counter resets on a rare/legendary hit.
PITY_RARE_AT: Final[int] = 30
PITY_LEGENDARY_AT: Final[int] = 50

# XP & leveling — our curve, tunable.
MAX_LEVEL: Final[int] = 30
XP_PER_LEVEL: Final[int] = 100  # flat curve; surplus rolls over on level-up
XP_PER_CHAT: Final[int] = 10
XP_PER_DUPLICATE: Final[int] = 25
XP_PER_STREAK_DAY: Final[int] = 15

# Milestone bonus pulls — level → cumulative pull grants, one-time per level.
LEVEL_MILESTONE_PULLS: Final[dict[int, int]] = {5: 1, 10: 2, 20: 3}
CHATS_PER_BONUS_PULL: Final[int] = 25
STREAK_DAYS_PER_BONUS_PULL: Final[int] = 7


def catalog_size() -> int:
    return len(CATALOG)


__all__ = [
    "CATALOG",
    "CATALOG_BY_ID",
    "CHATS_PER_BONUS_PULL",
    "LEVEL_MILESTONE_PULLS",
    "MAX_LEVEL",
    "PITY_LEGENDARY_AT",
    "PITY_RARE_AT",
    "RARITY_WEIGHTS",
    "STREAK_DAYS_PER_BONUS_PULL",
    "XP_PER_CHAT",
    "XP_PER_DUPLICATE",
    "XP_PER_LEVEL",
    "XP_PER_STREAK_DAY",
    "catalog_size",
]
