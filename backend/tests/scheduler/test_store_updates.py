"""`update_job` / `update_alert` patch semantics: only fields in `model_fields_set` are applied,
`None` clears the nullable ones, every edit bumps `version`, and the auto-pause / status reason is
cleared exactly by the edits that should clear it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scheduler.models import (
    AlertPatch,
    DeliveryChannelKind,
    JobStatus,
    QuietHours,
    ScheduleAction,
    SchedulePatch,
    ScheduleTrigger,
    TriggerMode,
    TriggerType,
)
from scheduler.store import (
    AlertRuleNotFoundError,
    ScheduleJobNotFoundError,
    SqlAlchemySchedulerStore,
    StaleVersionError,
)
from scheduler.templates import ClosePrice, InstitutionalStreak
from schemas.enums import Market, Tier
from tests.scheduler.conftest import insert_user

QUIET = QuietHours(start="22:00", end="07:00")


def _trigger() -> ScheduleTrigger:
    return ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec="after_close", market=Market.TW)


def _action() -> ScheduleAction:
    return ScheduleAction(skill_ref="daily-context-brief", inputs={"stock_ids": ["2330"]})


async def _job(session_factory):  # type: ignore[no-untyped-def]
    user = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        job = await store.create_job(
            user_id=user,
            trigger=_trigger(),
            action=_action(),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
            quiet_hours=QUIET,
            daily_limit=3,
        )
        await session.commit()
    return user, job


async def test_job_empty_patch_changes_nothing_but_the_version(session_factory) -> None:
    user, job = await _job(session_factory)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        edited = await store.update_job(user_id=user, job_id=job.id, patch=SchedulePatch())
    assert edited.version == job.version + 1
    assert edited.trigger == job.trigger
    assert edited.action == job.action
    assert edited.delivery_channels == job.delivery_channels
    assert edited.quiet_hours == QUIET
    assert edited.daily_limit == 3
    assert edited.enabled is True
    assert edited.next_run == job.next_run
    assert edited.updated_at >= job.updated_at


async def test_job_explicit_none_for_non_nullable_fields_leaves_them_alone(session_factory) -> None:
    user, job = await _job(session_factory)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        edited = await store.update_job(
            user_id=user,
            job_id=job.id,
            patch=SchedulePatch(enabled=None, trigger=None, action=None, delivery_channels=None),
        )
    assert edited.enabled is True
    assert edited.trigger == job.trigger
    assert edited.action == job.action
    assert edited.delivery_channels == job.delivery_channels


async def test_job_every_field_is_applied(session_factory) -> None:
    user, job = await _job(session_factory)
    trigger = ScheduleTrigger(type=TriggerType.CRON, spec="0 9 * * 1-5", market=Market.US)
    action = ScheduleAction(skill_ref="other-skill", inputs={"a": 1})
    next_run = datetime.now(UTC) + timedelta(hours=2)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        edited = await store.update_job(
            user_id=user,
            job_id=job.id,
            patch=SchedulePatch(
                enabled=False,
                trigger=trigger,
                action=action,
                delivery_channels=(DeliveryChannelKind.IN_APP, DeliveryChannelKind.LINE),
                quiet_hours=QuietHours(start="01:00", end="02:00"),
                daily_limit=9,
            ),
            next_run=next_run,
        )
        await session.commit()
    assert edited.enabled is False
    assert edited.trigger == trigger
    assert edited.action == action
    assert edited.delivery_channels == (DeliveryChannelKind.IN_APP, DeliveryChannelKind.LINE)
    assert edited.quiet_hours == QuietHours(start="01:00", end="02:00")
    assert edited.daily_limit == 9
    assert edited.next_run == next_run


async def test_job_quiet_hours_none_clears_both_ends(session_factory) -> None:
    user, job = await _job(session_factory)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        edited = await store.update_job(
            user_id=user, job_id=job.id, patch=SchedulePatch(quiet_hours=None)
        )
    assert edited.quiet_hours is None
    assert edited.daily_limit == 3


@pytest.mark.parametrize(
    ("patch", "cleared"),
    [
        (SchedulePatch(enabled=True), True),
        (SchedulePatch(trigger=_trigger()), True),
        (SchedulePatch(enabled=None), True),  # "enabled" is in fields_set even when None
        (SchedulePatch(daily_limit=5), False),
        (SchedulePatch(quiet_hours=None), False),
        (SchedulePatch(action=_action()), False),
        (SchedulePatch(), False),
    ],
)
async def test_job_status_reason_is_cleared_only_by_enable_or_trigger_edits(
    session_factory, patch: SchedulePatch, cleared: bool
) -> None:
    user, job = await _job(session_factory)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        await store.set_job_status(job_id=job.id, status=JobStatus.ERROR, reason="boom")
        edited = await store.update_job(user_id=user, job_id=job.id, patch=patch)
    assert edited.status_reason == (None if cleared else "boom")


async def test_job_edit_isolation_and_version_guard(session_factory) -> None:
    user, job = await _job(session_factory)
    other = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        patch = SchedulePatch()
        with pytest.raises(ScheduleJobNotFoundError):
            await store.update_job(user_id=other, job_id=job.id, patch=patch)
        with pytest.raises(StaleVersionError):
            await store.update_job(user_id=user, job_id=job.id, patch=patch, expected_version=7)
        ok = await store.update_job(
            user_id=user, job_id=job.id, patch=SchedulePatch(), expected_version=job.version
        )
    assert ok.version == job.version + 1


async def _alert(session_factory):  # type: ignore[no-untyped-def]
    user = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        rule = await store.create_alert(
            user_id=user,
            condition=ClosePrice(comparator="above", value=600),
            symbol="2330",
            market=Market.TW,
            trigger_mode=TriggerMode.ONCE,
            cooldown_seconds=60,
            explain=False,
            channels=(DeliveryChannelKind.IN_APP,),
            quiet_hours=QUIET,
            daily_limit=4,
        )
        await session.commit()
    return user, rule


async def test_alert_empty_patch_changes_nothing_but_the_version(session_factory) -> None:
    user, rule = await _alert(session_factory)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        edited = await store.update_alert(user_id=user, alert_id=rule.id, patch=AlertPatch())
    assert edited.version == rule.version + 1
    assert edited.condition == rule.condition
    assert edited.trigger_mode is TriggerMode.ONCE
    assert edited.cooldown_seconds == 60
    assert edited.channels == (DeliveryChannelKind.IN_APP,)
    assert edited.quiet_hours == QUIET
    assert edited.daily_limit == 4
    assert edited.explain is False
    assert edited.enabled is True


async def test_alert_explicit_none_for_non_nullable_fields_leaves_them_alone(
    session_factory,
) -> None:
    user, rule = await _alert(session_factory)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        edited = await store.update_alert(
            user_id=user,
            alert_id=rule.id,
            patch=AlertPatch(
                enabled=None, condition=None, trigger_mode=None, explain=None, channels=None
            ),
        )
    assert edited.enabled is True
    assert edited.condition == rule.condition
    assert edited.trigger_mode is TriggerMode.ONCE
    assert edited.explain is False
    assert edited.channels == (DeliveryChannelKind.IN_APP,)


async def test_alert_every_field_is_applied_and_nullable_fields_clear(session_factory) -> None:
    user, rule = await _alert(session_factory)
    expires = datetime.now(UTC) + timedelta(days=3)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        edited = await store.update_alert(
            user_id=user,
            alert_id=rule.id,
            patch=AlertPatch(
                enabled=False,
                trigger_mode=TriggerMode.EVERY_OCCURRENCE,
                cooldown_seconds=120,
                expires_at=expires,
                explain=True,
                channels=(DeliveryChannelKind.IN_APP, DeliveryChannelKind.LINE),
                quiet_hours=QuietHours(start="01:00", end="02:00"),
                daily_limit=8,
            ),
        )
        assert edited.enabled is False
        assert edited.trigger_mode is TriggerMode.EVERY_OCCURRENCE
        assert edited.cooldown_seconds == 120
        assert edited.expires_at is not None
        assert edited.explain is True
        assert edited.channels == (DeliveryChannelKind.IN_APP, DeliveryChannelKind.LINE)
        assert edited.quiet_hours == QuietHours(start="01:00", end="02:00")
        assert edited.daily_limit == 8
        cleared = await store.update_alert(
            user_id=user,
            alert_id=rule.id,
            patch=AlertPatch(
                cooldown_seconds=None, expires_at=None, quiet_hours=None, daily_limit=None
            ),
        )
    assert cleared.cooldown_seconds is None
    assert cleared.expires_at is None
    assert cleared.quiet_hours is None
    assert cleared.daily_limit is None


async def test_alert_new_condition_resets_the_edge_trigger_state(session_factory) -> None:
    user, rule = await _alert(session_factory)
    new_condition = InstitutionalStreak(investor="foreign", direction="buy", days=3)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        await store.save_alert_state(
            alert_id=rule.id, state={"met": {"2330": True}, "fire_count": 2}
        )
        kept = await store.update_alert(
            user_id=user, alert_id=rule.id, patch=AlertPatch(cooldown_seconds=5)
        )
        assert kept.state == {"met": {"2330": True}, "fire_count": 2}
        edited = await store.update_alert(
            user_id=user, alert_id=rule.id, patch=AlertPatch(condition=new_condition)
        )
    assert edited.state == {}
    assert edited.condition == new_condition


@pytest.mark.parametrize(
    ("patch", "cleared"),
    [
        (AlertPatch(enabled=True), True),
        (AlertPatch(condition=ClosePrice(comparator="below", value=1)), True),
        (AlertPatch(expires_at=None), True),
        (AlertPatch(trigger_mode=TriggerMode.ONCE_PER_DAY), False),
        (AlertPatch(cooldown_seconds=1), False),
        (AlertPatch(explain=True), False),
        (AlertPatch(channels=()), False),
        (AlertPatch(quiet_hours=None), False),
        (AlertPatch(daily_limit=1), False),
        (AlertPatch(), False),
    ],
)
async def test_alert_auto_pause_reason_is_cleared_only_by_enable_condition_or_expiry_edits(
    session_factory, patch: AlertPatch, cleared: bool
) -> None:
    user, rule = await _alert(session_factory)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        await store.save_alert_state(
            alert_id=rule.id, state={}, enabled=False, auto_paused_reason="expired"
        )
        edited = await store.update_alert(user_id=user, alert_id=rule.id, patch=patch)
    assert edited.auto_paused_reason == (None if cleared else "expired")


async def test_alert_edit_isolation_and_version_guard(session_factory) -> None:
    user, rule = await _alert(session_factory)
    other = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        patch = AlertPatch()
        with pytest.raises(AlertRuleNotFoundError):
            await store.update_alert(user_id=other, alert_id=rule.id, patch=patch)
        with pytest.raises(StaleVersionError):
            await store.update_alert(
                user_id=user, alert_id=rule.id, patch=patch, expected_version=7
            )
        ok = await store.update_alert(
            user_id=user, alert_id=rule.id, patch=AlertPatch(), expected_version=rule.version
        )
    assert ok.version == rule.version + 1
