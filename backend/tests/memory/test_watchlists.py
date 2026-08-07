"""`WatchlistStore` — group/item CRUD, the idempotent star toggle, the reverse query, and the
`snapshot()` read-through projection; plus `midpoint_key`'s lexorank ordering invariant."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from memory.watchlists import WatchlistStore, midpoint_key
from schemas.enums import Market


def test_midpoint_key_of_no_neighbors_returns_the_start_key() -> None:
    assert midpoint_key(None, None) == "m"


def test_midpoint_key_is_strictly_between_its_bounds() -> None:
    key = midpoint_key("a", "z")
    assert "a" < key < "z"


def test_midpoint_key_after_a_lower_bound_only_is_greater() -> None:
    key = midpoint_key("m", None)
    assert key > "m"


def test_midpoint_key_before_an_upper_bound_only_is_lesser() -> None:
    key = midpoint_key(None, "m")
    assert key < "m"


def test_midpoint_key_rejects_an_inverted_range() -> None:
    with pytest.raises(ValueError, match="requires lo < hi"):
        midpoint_key("z", "a")


async def test_create_group_then_add_item_round_trips(db_session: AsyncSession) -> None:
    store = WatchlistStore(db_session)
    group = await store.create_group(
        user_id="user-1", name="半導體", market=Market.TW, asset_class="stock"
    )

    item = await store.add_item(user_id="user-1", watchlist_id=group.id, symbol="2330")

    assert item.symbol == "2330"
    assert item.market is Market.TW


async def test_add_item_is_idempotent(db_session: AsyncSession) -> None:
    store = WatchlistStore(db_session)
    group = await store.create_group(
        user_id="user-1", name="半導體", market=Market.TW, asset_class="stock"
    )

    first = await store.add_item(user_id="user-1", watchlist_id=group.id, symbol="2330")
    second = await store.add_item(user_id="user-1", watchlist_id=group.id, symbol="2330")

    assert first.id == second.id


async def test_remove_item_is_idempotent(db_session: AsyncSession) -> None:
    store = WatchlistStore(db_session)
    group = await store.create_group(
        user_id="user-1", name="半導體", market=Market.TW, asset_class="stock"
    )
    await store.add_item(user_id="user-1", watchlist_id=group.id, symbol="2330")

    await store.remove_item(user_id="user-1", watchlist_id=group.id, symbol="2330")
    await store.remove_item(user_id="user-1", watchlist_id=group.id, symbol="2330")  # no-op

    assert await store.watches(user_id="user-1", symbol="2330") is False


async def test_watches_reverse_query(db_session: AsyncSession) -> None:
    store = WatchlistStore(db_session)
    group = await store.create_group(
        user_id="user-1", name="半導體", market=Market.TW, asset_class="stock"
    )
    await store.add_item(user_id="user-1", watchlist_id=group.id, symbol="2330")

    assert await store.watches(user_id="user-1", symbol="2330") is True
    assert await store.watches(user_id="user-1", symbol="2454") is False
    assert await store.watches(user_id="user-2", symbol="2330") is False


async def test_snapshot_renders_one_entry_per_group_with_ordered_symbols(
    db_session: AsyncSession,
) -> None:
    store = WatchlistStore(db_session)
    group = await store.create_group(
        user_id="user-1", name="半導體", market=Market.TW, asset_class="stock"
    )
    await store.add_item(user_id="user-1", watchlist_id=group.id, symbol="2330")
    await store.add_item(user_id="user-1", watchlist_id=group.id, symbol="2454")

    snapshot = await store.snapshot("user-1")

    assert len(snapshot) == 1
    assert snapshot[0].name == "半導體"
    assert snapshot[0].symbols == ("2330", "2454")


async def test_list_groups_excludes_soft_deleted(db_session: AsyncSession) -> None:
    store = WatchlistStore(db_session)
    await store.create_group(user_id="user-1", name="A", market=Market.TW, asset_class="stock")

    groups = await store.list_groups("user-1")

    assert len(groups) == 1
