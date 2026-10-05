"""Quiet hours are wall-clock in the user's own timezone; what they park is sent as one digest."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from accounts.hooks import EntitlementResolver
from ingest.leader import AlwaysLeader
from scheduler.delivery.base import DeliveryNotification, DeliveryReceipt, DeliveryRecipient
from scheduler.delivery.channels import InAppChannel
from scheduler.models import (
    DeliveryChannelKind,
    NotificationSource,
    QuietHours,
    ScheduleAction,
    ScheduleTrigger,
    TriggerType,
)
from scheduler.quota import AllowAllQuotaGate
from scheduler.store import SqlAlchemySchedulerStore
from scheduler.worker import SchedulerContext, SchedulerWorker, _within_quiet_hours
from schemas.alerts import NON_ADVICE_FOOTER
from schemas.enums import Tier
from tests.scheduler.conftest import insert_user
from tests.scheduler.test_worker import _CLEAN_ANSWER, _StubOrchestrator, _StubRouter

_NIGHT = QuietHours(start="22:00", end="07:00")


class _RecordingPush:
    kind = DeliveryChannelKind.PUSH

    def __init__(self) -> None:
        self.sent: list[DeliveryNotification] = []

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt:
        del recipient
        self.sent.append(notification)
        return DeliveryReceipt(channel=self.kind, delivered=True)


def _worker(
    session_factory,
    push: _RecordingPush | None = None,
    *,
    quota_gate=None,
    orchestrator=None,
    brief_tool=None,
) -> SchedulerWorker:
    channels: dict = {DeliveryChannelKind.IN_APP: InAppChannel()}
    if push is not None:
        channels[DeliveryChannelKind.PUSH] = push
    return SchedulerWorker(
        SchedulerContext(
            session_factory=session_factory,
            orchestrator=orchestrator or _StubOrchestrator(_CLEAN_ANSWER),  # type: ignore[arg-type]
            router=_StubRouter({}),  # type: ignore[arg-type]
            channels=channels,
            entitlement_resolver=EntitlementResolver(),
            leader=AlwaysLeader(),
            quota_gate=quota_gate or AllowAllQuotaGate(),
            brief_tool=brief_tool,
        )
    )


def test_quiet_window_is_evaluated_in_the_users_timezone_not_utc() -> None:
    # 15:00 UTC is 23:00 in Taipei (inside 22:00-07:00) but 08:00 in New York (outside).
    moment = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
    assert _within_quiet_hours(_NIGHT, moment, "Asia/Taipei")
    assert not _within_quiet_hours(_NIGHT, moment, "America/New_York")
    assert not _within_quiet_hours(None, moment, "Asia/Taipei")
    assert _within_quiet_hours(_NIGHT, moment, "Not/AZone")  # falls back to Asia/Taipei


async def _job_user(session_factory, *, quiet: QuietHours | None) -> str:
    user = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        await store.upsert_preferences(
            user_id=user,
            channels=(DeliveryChannelKind.IN_APP, DeliveryChannelKind.PUSH),
            enabled_categories=(),
            quiet_hours=quiet,
            morning_digest=False,
            timezone="Asia/Taipei",
        )
        await store.create_job(
            user_id=user,
            trigger=ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec="after_close"),
            action=ScheduleAction(skill_ref="daily-context-brief", inputs={"stock_ids": ["2330"]}),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
            next_run=datetime(2026, 10, 5, 0, 0, tzinfo=UTC),
        )
        await session.commit()
    return user


async def test_a_job_due_in_the_users_quiet_hours_waits_and_runs_after_the_window(
    session_factory,
) -> None:
    await _job_user(session_factory, quiet=_NIGHT)
    worker = _worker(session_factory)

    assert (
        await worker.run_due_jobs(now=datetime(2026, 10, 5, 15, 0, tzinfo=UTC)) == []
    )  # 23:00 TPE
    assert (
        len(await worker.run_due_jobs(now=datetime(2026, 10, 5, 23, 30, tzinfo=UTC))) == 1
    )  # 07:30


async def test_parked_external_notifications_leave_as_one_digest_after_the_window(
    session_factory,
) -> None:
    user = await _job_user(session_factory, quiet=_NIGHT)
    push = _RecordingPush()
    worker = _worker(session_factory, push)
    night = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)  # 23:00 Taipei

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        loaded = await worker._load_user(session, user)
        assert loaded is not None
        for n in (1, 2):
            await worker._deliver(
                store,
                user=loaded,
                source=NotificationSource.ALERT,
                source_id=f"rule-{n}",
                channels=(DeliveryChannelKind.PUSH,),
                title=f"2330 - 規則 {n}",
                body=f"條件 {n} 已符合",
                trigger_date=date(2026, 10, 5),
                deep_link={},
                hold_external=True,
            )
        await session.commit()

    assert push.sent == []  # parked, nothing left the building
    async with session_factory() as session:
        inbox = await SqlAlchemySchedulerStore(session).list_notifications(user_id=user)
    assert len(inbox) == 2  # the inbox entries are written at once, silently

    assert await worker.release_held(now=night) == 0  # window still open
    assert await worker.release_held(now=night + timedelta(hours=9)) == 2  # 08:00 Taipei
    assert len(push.sent) == 1  # ONE digest for both
    assert "2 則" in push.sent[0].title
    assert await worker.release_held(now=night + timedelta(hours=10)) == 0  # nothing left


async def test_every_external_send_carries_the_caveat_exactly_once(session_factory) -> None:
    user = await _job_user(session_factory, quiet=None)
    push = _RecordingPush()
    worker = _worker(session_factory, push)

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        loaded = await worker._load_user(session, user)
        assert loaded is not None
        # A scheduled-report preview arrives with no footer at all.
        await worker._deliver(
            store,
            user=loaded,
            source=NotificationSource.SCHEDULE,
            source_id="job-1",
            channels=(DeliveryChannelKind.PUSH,),
            title="排程摘要",
            body="台積電外資近期買超。",
            trigger_date=date(2026, 10, 5),
            deep_link={},
        )
        await session.commit()

    assert len(push.sent) == 1
    assert push.sent[0].body.count(NON_ADVICE_FOOTER) == 1


async def test_a_held_digest_has_one_footer_not_one_per_item(session_factory) -> None:
    user = await _job_user(session_factory, quiet=_NIGHT)
    push = _RecordingPush()
    worker = _worker(session_factory, push)
    night = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)

    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        loaded = await worker._load_user(session, user)
        assert loaded is not None
        for n in (1, 2):
            await worker._deliver(
                store,
                user=loaded,
                source=NotificationSource.ALERT,
                source_id=f"rule-{n}",
                channels=(DeliveryChannelKind.PUSH,),
                title=f"2330 - 規則 {n}",
                body=f"條件 {n} 已符合\n{NON_ADVICE_FOOTER}",
                trigger_date=date(2026, 10, 5),
                deep_link={},
                hold_external=True,
            )
        await session.commit()

    await worker.release_held(now=night + timedelta(hours=9))
    assert push.sent[0].body.count(NON_ADVICE_FOOTER) == 1


async def test_a_user_who_never_saved_preferences_is_quiet_from_midnight_to_eight_taipei(
    session_factory, monkeypatch
) -> None:
    from scheduler.models import DEFAULT_QUIET_HOURS  # noqa: PLC0415 — the real preset

    monkeypatch.setattr("scheduler.store.DEFAULT_QUIET_HOURS", DEFAULT_QUIET_HOURS)
    user = await insert_user(session_factory, tier=Tier.PRO)  # no preferences row at all
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        await store.create_job(
            user_id=user,
            trigger=ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec="after_close"),
            action=ScheduleAction(skill_ref="daily-context-brief", inputs={"stock_ids": ["2330"]}),
            delivery_channels=(DeliveryChannelKind.IN_APP,),
            next_run=datetime(2026, 10, 5, 0, 0, tzinfo=UTC),
        )
        await session.commit()
    worker = _worker(session_factory)

    assert (
        await worker.run_due_jobs(now=datetime(2026, 10, 5, 16, 30, tzinfo=UTC)) == []
    )  # 00:30 TPE
    assert (
        len(await worker.run_due_jobs(now=datetime(2026, 10, 5, 0, 30, tzinfo=UTC))) == 1
    )  # 08:30


async def test_a_suppressed_email_address_is_never_a_recipient(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        worker = _worker(session_factory)
        loaded = await worker._load_user(session, user)
        assert loaded is not None and loaded.email
        before = await worker._recipient_for(store, loaded)
        assert before.email_address == loaded.email
        await store.suppress_email(address=loaded.email.upper(), reason="bounced")
        after = await worker._recipient_for(store, loaded)
        assert after.email_address is None
