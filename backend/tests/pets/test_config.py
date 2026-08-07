"""Pet catalog invariants: exactly 15 pets, unique ids, and every rarity represented so the
gacha roll always has a candidate at each tier."""

from __future__ import annotations

from pets import config
from pets.models import Rarity

EXPECTED_CATALOG_SIZE = 15


def test_catalog_has_fifteen_pets() -> None:
    assert config.catalog_size() == EXPECTED_CATALOG_SIZE
    assert len(config.CATALOG) == EXPECTED_CATALOG_SIZE


def test_catalog_ids_are_unique() -> None:
    ids = [entry.pet_id for entry in config.CATALOG]
    assert len(set(ids)) == EXPECTED_CATALOG_SIZE
    assert len(config.CATALOG_BY_ID) == EXPECTED_CATALOG_SIZE


def test_every_rarity_has_at_least_one_pet() -> None:
    rarities = {entry.rarity for entry in config.CATALOG}
    assert rarities == set(Rarity)


def test_rarity_weights_cover_all_rarities() -> None:
    assert set(config.RARITY_WEIGHTS) == set(Rarity)
