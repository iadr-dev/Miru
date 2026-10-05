"""Fresh-schema guarantees for alerts v2 (modules/14): scope XOR, trigger-mode CHECK, watchlist
cascade, new-field round trip, web push platform, timezone preference, mark-read."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from db.models import AlertRuleRow, WatchlistRow
from scheduler.models import (
    AlertRule,
    DeliveryChannelKind,
    DevicePlatform,
    NotificationSource,
    TriggerMode,
)
from scheduler.store import SqlAlchemySchedulerStore
from scheduler.templates import ClosePrice
from schemas.enums import Market, Tier
from tests.scheduler.conftest import insert_user


def _condition() -> ClosePrice:
    return ClosePrice(comparator="above", value=600)


async def _watchlist(session_factory, user_id: str) -> str:
    now = datetime.now(UTC)
    async with session_factory() as session:
        row = WatchlistRow(
            id="wl-1",
            user_id=user_id,
            name="自選",
            market="TW",
            asset_class="stock",
            sort_order="a",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
    return "wl-1"


def test_domain_scope_requires_symbol_xor_watchlist() -> None:
    now = datetime.now(UTC)
    base = {
        "id": "a",
        "user_id": "u",
        "condition": _condition(),
        "created_at": now,
        "updated_at": now,
    }
    AlertRule(**base, symbol="2330", market=Market.TW)  # ok
    AlertRule(**base, watchlist_id="wl")  # ok
    with pytest.raises(ValidationError):
        AlertRule(**base)  # neither
    with pytest.raises(ValidationError):
        AlertRule(**base, symbol="2330", market=Market.TW, watchlist_id="wl")  # both
    with pytest.raises(ValidationError):
        AlertRule(**base, symbol="2330")  # symbol without market


async def test_symbol_rule_round_trips_new_fields(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    expires = datetime.now(UTC) + timedelta(days=90)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        created = await store.create_alert(
            user_id=user,
            condition=_condition(),
            symbol="2330",
            market=Market.TW,
            channels=(DeliveryChannelKind.IN_APP,),
            trigger_mode=TriggerMode.ONCE,
            cooldown_seconds=3600,
            expires_at=expires,
            explain=True,
        )
        await session.commit()
    async with session_factory() as session:
        fetched = await SqlAlchemySchedulerStore(session).get_alert(
            user_id=user, alert_id=created.id
        )
    assert fetched.trigger_mode is TriggerMode.ONCE
    assert fetched.cooldown_seconds == 3600
    assert fetched.explain is True
    assert fetched.version == 0
    assert fetched.state == {}
    assert fetched.watchlist_id is None
    assert fetched.expires_at is not None


async def test_watchlist_scoped_rule_and_cascade(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    wl = await _watchlist(session_factory, user)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        rule = await store.create_alert(
            user_id=user, condition=_condition(), watchlist_id=wl, channels=()
        )
        await session.commit()
    assert rule.symbol is None
    assert rule.watchlist_id == wl

    # Deleting the watchlist removes its rules (ON DELETE CASCADE) — SQLite needs FKs on.
    async with session_factory() as session:
        await session.execute(delete(WatchlistRow).where(WatchlistRow.id == wl))
        await session.commit()
    async with session_factory() as session:
        assert await SqlAlchemySchedulerStore(session).list_alerts(user_id=user) == []


async def test_db_rejects_bad_scope_and_trigger_mode(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    now = datetime.now(UTC)
    common = {
        "user_id": user,
        "condition": {},
        "channels": [],
        "created_at": now,
        "updated_at": now,
    }
    async with session_factory() as session:
        session.add(AlertRuleRow(id="both", symbol="2330", market="TW", watchlist_id="x", **common))
        with pytest.raises(IntegrityError):
            await session.commit()
    async with session_factory() as session:
        session.add(AlertRuleRow(id="neither", **common))
        with pytest.raises(IntegrityError):
            await session.commit()
    async with session_factory() as session:
        session.add(
            AlertRuleRow(id="mode", symbol="2330", market="TW", trigger_mode="hourly", **common)
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_web_push_device_and_timezone_preference(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.FREE)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        device = await store.register_device(
            user_id=user, platform=DevicePlatform.WEB, token='{"endpoint":"https://x"}'
        )
        pref = await store.upsert_preferences(
            user_id=user,
            channels=(DeliveryChannelKind.IN_APP,),
            enabled_categories=(),
            quiet_hours=None,
            morning_digest=False,
            timezone="America/New_York",
        )
        await session.commit()
    assert device.platform is DevicePlatform.WEB
    assert pref.timezone == "America/New_York"
    async with session_factory() as session:
        default = await SqlAlchemySchedulerStore(session).get_preferences(user_id=user)
    assert default is not None
    assert default.timezone == "America/New_York"


async def test_mark_read_scopes_to_user_and_is_idempotent(session_factory) -> None:
    owner = await insert_user(session_factory, tier=Tier.FREE)
    other = await insert_user(session_factory, tier=Tier.FREE)
    today = datetime.now(UTC).date()
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        mine = []
        for i in range(3):
            n = await store.record_notification(
                user_id=owner,
                source=NotificationSource.ALERT,
                source_id=f"a{i}",
                channel=DeliveryChannelKind.IN_APP,
                trigger_date=today,
                payload={"title": "t"},
            )
            assert n is not None
            mine.append(n.id)
        theirs = await store.record_notification(
            user_id=other,
            source=NotificationSource.ALERT,
            source_id="z",
            channel=DeliveryChannelKind.IN_APP,
            trigger_date=today,
            payload={"title": "t"},
        )
        assert theirs is not None
        await session.commit()

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        # Another user's id in the list is ignored, never marked.
        assert await store.mark_notifications_read(user_id=owner, ids=(mine[0], theirs.id)) == 1
        assert await store.mark_notifications_read(user_id=owner, ids=(mine[0],)) == 0
        assert await store.mark_notifications_read(user_id=owner) == 2
        assert (await store.badge_counts(user_id=owner)).unread_notifications == 0
        assert (await store.badge_counts(user_id=other)).unread_notifications == 1
