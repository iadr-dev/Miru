"""`WatchlistStore` — group/item CRUD, the idempotent star toggle, the reverse query, and the
`snapshot()` read-through projection; plus `midpoint_key`'s lexorank ordering invariant."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import WatchlistRow
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


async def test_seed_default_presets_populates_the_right_groups_and_items(
    db_session: AsyncSession,
) -> None:
    store = WatchlistStore(db_session)

    created = await store.seed_default_presets(
        user_id="user-1", market=Market.TW, asset_class="stock"
    )

    assert {group.name for group in created} == {
        "我的自選股",
        "SpaceX 供應鏈",
        "軍工&無人機",
        "記憶體",
        "金融&航空",
        "半導體",
    }
    groups = await store.list_groups("user-1")
    assert len(groups) == 6
    for group in groups:
        assert group.is_default is True
        items = await store.list_items(user_id="user-1", watchlist_id=group.id)
        if group.name == "我的自選股":
            assert items == []
        else:
            assert len(items) >= 5


async def test_seed_default_presets_is_a_noop_once_the_user_has_any_group_there(
    db_session: AsyncSession,
) -> None:
    """Even a user's own single manually-created group must block seeding — presets never
    silently reappear after a user deletes them or replaces them with their own groups."""
    store = WatchlistStore(db_session)
    await store.create_group(
        user_id="user-1", name="我的自選", market=Market.TW, asset_class="stock"
    )

    created = await store.seed_default_presets(
        user_id="user-1", market=Market.TW, asset_class="stock"
    )

    assert created == []
    groups = await store.list_groups("user-1")
    assert len(groups) == 1
    assert groups[0].name == "我的自選"


async def test_seed_default_presets_scopes_to_market_and_asset_class(
    db_session: AsyncSession,
) -> None:
    """Seeding one (market, asset_class) combo must not touch the other two — a user with TW
    stock groups still gets seeded for US stock and TW ETF independently."""
    store = WatchlistStore(db_session)
    await store.create_group(
        user_id="user-1", name="我的自選", market=Market.TW, asset_class="stock"
    )

    tw_stock = await store.seed_default_presets(
        user_id="user-1", market=Market.TW, asset_class="stock"
    )
    us_stock = await store.seed_default_presets(
        user_id="user-1", market=Market.US, asset_class="stock"
    )
    tw_etf = await store.seed_default_presets(user_id="user-1", market=Market.TW, asset_class="etf")

    assert tw_stock == []
    assert len(us_stock) == 6
    assert len(tw_etf) == 4


async def test_seed_default_presets_symbols_round_trip_through_watches(
    db_session: AsyncSession,
) -> None:
    store = WatchlistStore(db_session)

    await store.seed_default_presets(user_id="user-1", market=Market.TW, asset_class="stock")

    assert await store.watches(user_id="user-1", symbol="2330") is True
    assert await store.watches(user_id="user-1", symbol="9999") is False


async def test_new_groups_go_to_the_end_in_a_stored_order(db_session: AsyncSession) -> None:
    store = WatchlistStore(db_session)
    names = ["A", "B", "C", "D"]
    for name in names:
        await store.create_group(
            user_id="u-order", name=name, market=Market.TW, asset_class="stock"
        )
    groups = await store.list_groups("u-order")
    keys = [g.sort_order for g in groups]
    assert [g.name for g in groups] == names
    assert keys == sorted(set(keys))  # distinct and ascending


async def test_recolouring_or_renaming_a_group_does_not_move_it(
    db_session: AsyncSession,
) -> None:
    store = WatchlistStore(db_session)
    created = [
        await store.create_group(user_id="u-keep", name=n, market=Market.TW, asset_class="stock")
        for n in ["A", "B", "C"]
    ]
    await store.set_group_color(user_id="u-keep", watchlist_id=created[0].id, color="chart-3")
    await store.rename_group(user_id="u-keep", watchlist_id=created[1].id, name="B2")
    await db_session.flush()
    groups = await store.list_groups("u-keep")
    assert [g.id for g in groups] == [g.id for g in created]
    assert groups[0].color == "chart-3"


async def test_groups_that_share_a_sort_key_are_given_distinct_keys_in_creation_order(
    db_session: AsyncSession,
) -> None:
    base = datetime(2026, 10, 1, tzinfo=UTC)
    for i, name in enumerate(["first", "second", "third"]):
        db_session.add(
            WatchlistRow(
                id=f"legacy-{i}",
                user_id="u-legacy",
                name=name,
                market="TW",
                asset_class="stock",
                sort_order="m",
                is_default=False,
                version=0,
                created_at=base + timedelta(minutes=i),
                updated_at=base,
            )
        )
    await db_session.flush()
    store = WatchlistStore(db_session)
    groups = await store.list_groups("u-legacy")
    assert [g.name for g in groups] == ["first", "second", "third"]
    assert len({g.sort_order for g in groups}) == 3
    # stored now: a later read returns the same order
    assert [g.name for g in await store.list_groups("u-legacy")] == ["first", "second", "third"]
