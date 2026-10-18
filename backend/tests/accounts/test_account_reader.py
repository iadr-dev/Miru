"""`SqlAccountDataReader` over a real (SQLite) database with two users: each read returns only the
acting user's rows, reusing the same stores the REST routes use."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from accounts.account_reader import SqlAccountDataReader
from config.settings import Settings
from db import (
    models,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    rls,
)
from db.base import Base
from db.models import UserRow
from db.sqlite_pragma import enable_sqlite_foreign_keys
from memory.watchlists import WatchlistStore
from scheduler.models import (
    DeliveryChannelKind,
    NotificationSource,
    ScheduleAction,
    ScheduleTrigger,
    TriggerType,
)
from scheduler.store import SqlAlchemySchedulerStore
from scheduler.strategy_store import StrategySubscriptionStore
from scheduler.templates import ClosePrice
from schemas.enums import Market, Tier
from tools.account_tools import (
    MAX_NOTIFICATION_BODY,
    GetMyAutomationsInput,
    GetMyNotificationsInput,
    GetMyUsageInput,
    GetMyWatchlistsInput,
    GetMyWatchlistsOutput,
    build_account_tools,
)
from tools.base import SignInRequired, ToolContext, dispatch_tool
from tools.registry import ToolRegistry
from usage.store import SqlAlchemyUsageStore

TODAY = date(2026, 10, 18)


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _user(factory: async_sessionmaker[AsyncSession], tier: Tier = Tier.FREE) -> str:
    now = datetime.now(UTC)
    user_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(
            UserRow(
                id=user_id,
                email=f"{user_id}@example.test",
                display_name="Test",
                tier=tier.value,
                personality_id="steady",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return user_id


def _reader(factory: async_sessionmaker[AsyncSession]) -> SqlAccountDataReader:
    return SqlAccountDataReader(
        factory,
        usage_store=SqlAlchemyUsageStore(factory),
        settings=Settings(_env_file=None),
        clock=lambda: datetime(2026, 10, 18, 9, 0, tzinfo=UTC),
    )


async def _group(
    factory: async_sessionmaker[AsyncSession], user: str, name: str, symbols: list[str]
) -> str:
    async with factory() as session:
        store = WatchlistStore(session)
        group = await store.create_group(
            user_id=user, name=name, market=Market.TW, asset_class="stock"
        )
        for symbol in symbols:
            await store.add_item(user_id=user, watchlist_id=group.id, symbol=symbol)
        await session.commit()
    return group.id


async def test_watchlists_return_only_the_acting_users_groups(session_factory) -> None:
    alice = await _user(session_factory)
    bob = await _user(session_factory)
    await _group(session_factory, alice, "半導體", ["2330", "2454"])
    await _group(session_factory, bob, "Bob private", ["9999"])
    reader = _reader(session_factory)

    snapshot = await reader.watchlists(user_id=alice, market=None, max_items=200)

    assert [g.name for g in snapshot.groups] == ["半導體"]
    assert [i.symbol for i in snapshot.groups[0].items] == ["2330", "2454"]
    assert snapshot.total_items == 2 and not snapshot.truncated
    flat = snapshot.model_dump_json()
    assert "Bob private" not in flat and "9999" not in flat


async def test_watchlists_are_capped_and_filtered_by_market(session_factory) -> None:
    alice = await _user(session_factory)
    await _group(session_factory, alice, "big", [str(1000 + i) for i in range(30)])
    async with session_factory() as session:
        store = WatchlistStore(session)
        us = await store.create_group(
            user_id=alice, name="us", market=Market.US, asset_class="stock"
        )
        await store.add_item(user_id=alice, watchlist_id=us.id, symbol="AAPL")
        await session.commit()
    reader = _reader(session_factory)

    capped = await reader.watchlists(user_id=alice, market=None, max_items=10)
    assert capped.truncated and capped.total_items == 31
    assert sum(len(g.items) for g in capped.groups) == 10
    assert {g.name: g.item_count for g in capped.groups} == {"big": 30, "us": 1}

    only_us = await reader.watchlists(user_id=alice, market=Market.US, max_items=200)
    assert [g.name for g in only_us.groups] == ["us"]


async def test_a_user_with_nothing_gets_empty_results_not_someone_elses(session_factory) -> None:
    alice = await _user(session_factory)
    bob = await _user(session_factory)
    await _group(session_factory, bob, "b", ["2330"])
    reader = _reader(session_factory)

    assert (await reader.watchlists(user_id=alice, market=None, max_items=200)).groups == ()
    automations = await reader.automations(user_id=alice, max_alerts=100, max_schedules=50)
    assert automations.alerts == () and automations.schedules == ()
    assert automations.strategy_watch is None and automations.alert_count == 0
    assert await reader.notifications(user_id=alice, limit=20, unread_only=False) == []


async def test_automations_describe_alerts_schedules_and_strategy_watch(session_factory) -> None:
    alice = await _user(session_factory, Tier.PREMIUM)
    bob = await _user(session_factory)
    group_id = await _group(session_factory, alice, "我的群組", ["2330"])
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        live = await store.create_alert(
            user_id=alice,
            condition=ClosePrice(comparator="above", value=1000),
            symbol="2330",
            market=Market.TW,
            channels=(DeliveryChannelKind.IN_APP,),
        )
        await store.create_alert(
            user_id=alice,
            condition=ClosePrice(comparator="below", value=500),
            symbol="2317",
            market=Market.TW,
            expires_at=datetime(2026, 1, 1, tzinfo=UTC),
            channels=(DeliveryChannelKind.IN_APP,),
        )
        await store.create_alert(
            user_id=bob,
            condition=ClosePrice(comparator="above", value=77777),
            symbol="BOBSTOCK",
            market=Market.TW,
            channels=(DeliveryChannelKind.IN_APP,),
        )
        await store.mark_alert_triggered(alert_id=live.id, when=datetime(2026, 10, 17, tzinfo=UTC))
        await store.create_job(
            user_id=alice,
            trigger=ScheduleTrigger(
                type=TriggerType.MARKET_EVENT, spec="after_close", market=Market.TW
            ),
            action=ScheduleAction(skill_ref="daily-context-brief", inputs={"stock_ids": ["2330"]}),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
        )
        await store.create_job(
            user_id=bob,
            trigger=ScheduleTrigger(
                type=TriggerType.MARKET_EVENT, spec="pre_open", market=Market.TW
            ),
            action=ScheduleAction(skill_ref="bob-only-skill"),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
        )
        await StrategySubscriptionStore(session).put(
            user_id=alice,
            strategies=("volatility_squeeze",),
            watchlist_id=group_id,
            morning_list=True,
            channels=(),
            quiet_hours=None,
            daily_limit=5,
            enabled=True,
        )
        await session.commit()
    reader = _reader(session_factory)

    out = await reader.automations(user_id=alice, max_alerts=100, max_schedules=50)

    assert out.alert_count == 2
    by_target = {a.target: a for a in out.alerts}
    assert by_target["2330 TW"].condition == "收盤價高於 1000"  # the existing describe() wording
    assert by_target["2330 TW"].status == "active"
    assert by_target["2330 TW"].last_triggered is not None
    assert by_target["2317 TW"].status == "expired"
    assert out.schedule_count == 1 and out.schedules[0].briefing == "daily-context-brief"
    assert out.schedules[0].status == "active"
    assert out.strategy_watch is not None
    assert out.strategy_watch.strategies == ("波動率擠壓",)
    assert out.strategy_watch.scope == "watchlist group '我的群組'"
    assert out.strategy_watch.daily_limit == 5
    dumped = out.model_dump_json()
    assert "BOBSTOCK" not in dumped and "77777" not in dumped and "bob-only-skill" not in dumped

    capped = await reader.automations(user_id=alice, max_alerts=1, max_schedules=50)
    assert len(capped.alerts) == 1 and capped.alert_count == 2


async def test_a_paused_alert_reports_why(session_factory) -> None:
    alice = await _user(session_factory)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        rule = await store.create_alert(
            user_id=alice,
            condition=ClosePrice(comparator="above", value=10),
            symbol="2330",
            market=Market.TW,
            channels=(DeliveryChannelKind.IN_APP,),
        )
        from scheduler.models import AlertPatch  # noqa: PLC0415

        await store.update_alert(
            user_id=alice,
            alert_id=rule.id,
            patch=AlertPatch(enabled=False),
            expected_version=None,
        )
        await session.commit()

    out = await _reader(session_factory).automations(user_id=alice, max_alerts=10, max_schedules=5)
    assert out.alerts[0].status == "paused"


async def test_usage_reports_the_tier_limit_and_todays_count(session_factory) -> None:
    free = await _user(session_factory)
    pro = await _user(session_factory, Tier.PRO)
    other = await _user(session_factory)
    store = SqlAlchemyUsageStore(session_factory)
    for _ in range(2):
        await store.reserve_daily_chat(user_id=free, limit=3)
    await store.reserve_daily_chat(user_id=other, limit=3)
    reader = _reader(session_factory)

    chat = (await reader.usage(user_id=free, tier=Tier.FREE, include_llm=False)).chat
    assert (chat.used_today, chat.limit_per_day, chat.remaining_today) == (2, 3, 1)
    assert chat.unlimited_because is None

    unlimited = (await reader.usage(user_id=pro, tier=Tier.PRO, include_llm=False)).chat
    assert unlimited.limit_per_day is None and unlimited.remaining_today is None
    assert unlimited.unlimited_because == "tier" and unlimited.used_today == 0

    snapshot = await reader.usage(user_id=free, tier=Tier.FREE, include_llm=True)
    assert snapshot.llm_today is not None and snapshot.llm_today.calls == 0
    assert (await reader.usage(user_id=free, tier=Tier.FREE, include_llm=False)).llm_today is None


async def test_notifications_are_the_users_own_newest_first_and_only_display_fields(
    session_factory,
) -> None:
    alice = await _user(session_factory)
    bob = await _user(session_factory)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        for i in range(3):
            await store.record_notification(
                user_id=alice,
                source=NotificationSource.ALERT,
                source_id=f"a{i}",
                channel=DeliveryChannelKind.IN_APP,
                trigger_date=TODAY + timedelta(days=i),
                payload={
                    "title": f"title {i}",
                    "body": "x" * 1000,
                    "deep_link": "/secret/path",
                },
            )
        await store.record_notification(
            user_id=bob,
            source=NotificationSource.ALERT,
            source_id="b",
            channel=DeliveryChannelKind.IN_APP,
            trigger_date=TODAY,
            payload={"title": "bob title", "body": "bob body"},
        )
        await session.commit()
    reader = _reader(session_factory)

    rows = await reader.notifications(user_id=alice, limit=2, unread_only=False)

    assert len(rows) == 2
    assert all(r.title.startswith("title") for r in rows)
    assert all(len(r.body) <= MAX_NOTIFICATION_BODY for r in rows)
    assert all(not r.read for r in rows)
    dumped = " ".join(r.model_dump_json() for r in rows)
    assert "bob" not in dumped and "/secret/path" not in dumped
    assert rows[0].delivered_at >= rows[1].delivered_at


async def test_reading_marks_nothing_read(session_factory) -> None:
    alice = await _user(session_factory)
    async with session_factory() as session:
        await SqlAlchemySchedulerStore(session).record_notification(
            user_id=alice,
            source=NotificationSource.ALERT,
            source_id="a",
            channel=DeliveryChannelKind.IN_APP,
            trigger_date=TODAY,
            payload={"title": "t", "body": "b"},
        )
        await session.commit()
    reader = _reader(session_factory)
    await reader.notifications(user_id=alice, limit=20, unread_only=False)
    again = await reader.notifications(user_id=alice, limit=20, unread_only=True)
    assert len(again) == 1


async def test_the_reader_refuses_to_act_for_a_user_other_than_the_bound_one(
    session_factory,
) -> None:
    alice = await _user(session_factory)
    bob = await _user(session_factory)
    reader = _reader(session_factory)
    with rls.acting_as(alice), pytest.raises(PermissionError):
        await reader.watchlists(user_id=bob, market=None, max_items=10)
    # ... and the scoped binding does not leak out of a read.
    await reader.watchlists(user_id=bob, market=None, max_items=10)
    assert rls.current_user_id() is None


async def test_through_the_tools_a_forged_user_argument_never_reaches_another_account(
    session_factory,
) -> None:
    alice = await _user(session_factory)
    bob = await _user(session_factory)
    await _group(session_factory, alice, "alice list", ["2330"])
    await _group(session_factory, bob, "bob list", ["2317"])
    tools = {t.name: t for t in build_account_tools(_reader(session_factory), ToolRegistry())}
    ctx = ToolContext(user_id=alice)

    forged: dict[str, object] = {"user_id": bob}
    with pytest.raises(ValueError):
        tools["get_my_watchlists"].input_model.model_validate(forged)

    result = await dispatch_tool(tools["get_my_watchlists"], GetMyWatchlistsInput(), ctx)
    assert isinstance(result, GetMyWatchlistsOutput)
    assert [g.name for g in result.groups] == ["alice list"]

    anonymous = await dispatch_tool(
        tools["get_my_watchlists"], GetMyWatchlistsInput(), ToolContext()
    )
    assert isinstance(anonymous, SignInRequired)

    for name, args in (
        ("get_my_automations", GetMyAutomationsInput()),
        ("get_my_usage", GetMyUsageInput()),
        ("get_my_notifications", GetMyNotificationsInput()),
    ):
        out = await dispatch_tool(tools[name], args, ctx)
        assert isinstance(out, BaseModel) and "bob" not in out.model_dump_json()
