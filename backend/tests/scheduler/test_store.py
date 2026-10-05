"""SchedulerStore CRUD + per-user isolation, idempotent delivery dedupe, and device-token
registration idempotency."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from scheduler.models import (
    DeliveryChannelKind,
    DevicePlatform,
    JobStatus,
    NotificationSource,
    ScheduleAction,
    SchedulePatch,
    ScheduleTrigger,
    TriggerType,
)
from scheduler.store import (
    RUN_TIMEOUT,
    AlertRuleNotFoundError,
    ScheduleJobNotFoundError,
    SqlAlchemySchedulerStore,
    StaleVersionError,
)
from scheduler.templates import InstitutionalStreak
from schemas.enums import Market, Tier
from tests.scheduler.conftest import insert_user


def _trigger() -> ScheduleTrigger:
    return ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec="after_close", market=Market.TW)


def _action() -> ScheduleAction:
    return ScheduleAction(skill_ref="daily-context-brief", inputs={"stock_ids": ["2330"]})


def _condition() -> InstitutionalStreak:
    return InstitutionalStreak(investor="foreign", direction="buy", days=3)


async def test_job_crud_and_isolation(session_factory) -> None:
    user_a = await insert_user(session_factory, tier=Tier.PRO)
    user_b = await insert_user(session_factory, tier=Tier.PRO)

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        job = await store.create_job(
            user_id=user_a,
            trigger=_trigger(),
            action=_action(),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
        )
        await session.commit()

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        # user_b can't see user_a's job
        assert await store.list_jobs(user_id=user_b) == []
        assert len(await store.list_jobs(user_id=user_a)) == 1
        # user_b can't fetch it by id
        with pytest.raises(ScheduleJobNotFoundError):
            await store.get_job(user_id=user_b, job_id=job.id)


async def test_job_update_and_delete(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        job = await store.create_job(
            user_id=user,
            trigger=_trigger(),
            action=_action(),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
        )
        await session.commit()

        updated = await store.update_job(
            user_id=user, job_id=job.id, patch=SchedulePatch(enabled=False)
        )
        assert updated.enabled is False
        assert updated.version == job.version + 1
        await session.commit()

        await store.delete_job(user_id=user, job_id=job.id)
        await session.commit()
        assert await store.list_jobs(user_id=user) == []


async def test_alert_crud_and_isolation(session_factory) -> None:
    user_a = await insert_user(session_factory, tier=Tier.PRO)
    user_b = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        alert = await store.create_alert(
            user_id=user_a,
            symbol="2330",
            market=Market.TW,
            condition=_condition(),
            channels=(DeliveryChannelKind.IN_APP,),
        )
        await session.commit()

        assert await store.list_alerts(user_id=user_b) == []
        with pytest.raises(AlertRuleNotFoundError):
            await store.get_alert(user_id=user_b, alert_id=alert.id)
        roundtrip = await store.get_alert(user_id=user_a, alert_id=alert.id)
        assert roundtrip.condition.kind == "institutional_streak"


async def test_notification_dedupe_idempotent(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    trigger_date = date(2026, 8, 4)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        first = await store.record_notification(
            user_id=user,
            source=NotificationSource.ALERT,
            source_id="alert-1",
            channel=DeliveryChannelKind.IN_APP,
            trigger_date=trigger_date,
            payload={"body": "obs"},
        )
        await session.commit()
        assert first is not None

        # Redelivery of same (source_id, trigger_date, channel) is a no-op.
        second = await store.record_notification(
            user_id=user,
            source=NotificationSource.ALERT,
            source_id="alert-1",
            channel=DeliveryChannelKind.IN_APP,
            trigger_date=trigger_date,
            payload={"body": "obs"},
        )
        await session.commit()
        assert second is None

        # A different day is a distinct delivery.
        third = await store.record_notification(
            user_id=user,
            source=NotificationSource.ALERT,
            source_id="alert-1",
            channel=DeliveryChannelKind.IN_APP,
            trigger_date=trigger_date + timedelta(days=1),
            payload={"body": "obs"},
        )
        await session.commit()
        assert third is not None

        assert len(await store.list_notifications(user_id=user)) == 2


async def test_device_registration_idempotent(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        first = await store.register_device(
            user_id=user, platform=DevicePlatform.IOS, token="tok-abc"
        )
        await session.commit()
        second = await store.register_device(
            user_id=user, platform=DevicePlatform.IOS, token="tok-abc"
        )
        await session.commit()
        # Same token → same row (last_seen refreshed), never a duplicate.
        assert first.id == second.id
        assert second.last_seen >= first.last_seen
        assert len(await store.list_devices(user_id=user)) == 1

        await store.unregister_device(user_id=user, token="tok-abc")
        await session.commit()
        assert await store.list_devices(user_id=user) == []


async def test_badge_and_due_jobs(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    past = datetime.now(UTC) - timedelta(minutes=5)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        job = await store.create_job(
            user_id=user,
            trigger=_trigger(),
            action=_action(),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
            next_run=past,
        )
        await store.create_alert(
            user_id=user,
            symbol="2330",
            market=Market.TW,
            condition=_condition(),
            channels=(DeliveryChannelKind.IN_APP,),
        )
        await session.commit()

        due = await store.due_jobs(now=datetime.now(UTC))
        assert [j.id for j in due] == [job.id]

        badge = await store.badge_counts(user_id=user)
        assert badge.pending_alerts == 1


async def test_job_edit_changes_only_sent_fields_and_bumps_version(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        job = await store.create_job(
            user_id=user,
            trigger=_trigger(),
            action=_action(),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
            daily_limit=3,
        )
        new_trigger = ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec="pre_open")
        next_run = datetime.now(UTC) + timedelta(hours=5)

        edited = await store.update_job(
            user_id=user,
            job_id=job.id,
            patch=SchedulePatch(trigger=new_trigger),
            next_run=next_run,
            expected_version=job.version,
        )

        assert edited.trigger.spec == "pre_open"
        assert edited.next_run == next_run
        assert edited.daily_limit == 3  # not sent -> untouched
        assert edited.delivery_channels == (DeliveryChannelKind.IN_APP,)
        assert edited.version == job.version + 1

        cleared = await store.update_job(
            user_id=user, job_id=job.id, patch=SchedulePatch(daily_limit=None)
        )
        assert cleared.daily_limit is None  # sent as null -> cleared


async def test_stale_job_edit_is_refused(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        job = await store.create_job(
            user_id=user,
            trigger=_trigger(),
            action=_action(),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
        )
        await store.update_job(
            user_id=user, job_id=job.id, patch=SchedulePatch(enabled=False), expected_version=0
        )
        with pytest.raises(StaleVersionError):
            await store.update_job(
                user_id=user,
                job_id=job.id,
                patch=SchedulePatch(enabled=True),
                expected_version=0,
            )


async def test_due_jobs_skips_a_fresh_running_mark_but_recovers_an_orphaned_one(
    session_factory,
) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    now = datetime.now(UTC)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        job = await store.create_job(
            user_id=user,
            trigger=_trigger(),
            action=_action(),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
            next_run=now - timedelta(minutes=1),
        )
        await store.set_job_status(job_id=job.id, status=JobStatus.RUNNING)
        await session.commit()

        assert await store.due_jobs(now=now) == []  # mid-run: no duplicate start
        orphaned = await store.due_jobs(now=now + RUN_TIMEOUT + timedelta(minutes=1))
        assert [j.id for j in orphaned] == [job.id]  # crashed run: picked up again
