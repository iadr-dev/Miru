"""External delivery is durable and retried.

Before: the notification row and the send shared one transaction that committed at the end of the
pass, so a crash after a send (or a failure in a later rule) rolled the row back and the next pass
sent it again, and a failed send was only logged, never retried. Now the row is committed before
the send (it is the dedupe guard and the retry queue), and the outcome is recorded after it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from accounts.hooks import EntitlementResolver
from accounts.store import SqlAlchemyUserStore
from db.models import NotificationRow
from ingest.leader import AlwaysLeader, NeverLeader
from scheduler.delivery.base import DeliveryNotification, DeliveryReceipt, DeliveryRecipient
from scheduler.delivery.channels import InAppChannel
from scheduler.models import DeliveryChannelKind, NotificationSource
from scheduler.quota import AllowAllQuotaGate
from scheduler.store import SqlAlchemySchedulerStore
from scheduler.worker import SchedulerContext, SchedulerWorker
from schemas.enums import Tier
from tests.scheduler.conftest import insert_user


class _ScriptedChannel:
    """An external channel whose outcomes are scripted: a receipt to return, or an exception."""

    kind = DeliveryChannelKind.EMAIL

    def __init__(self, *outcomes: DeliveryReceipt | Exception) -> None:
        self._outcomes = list(outcomes)
        self.sent: list[str] = []

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt:
        self.sent.append(notification.notification_id)
        outcome = self._outcomes.pop(0) if self._outcomes else _ok()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ok() -> DeliveryReceipt:
    return DeliveryReceipt(channel=DeliveryChannelKind.EMAIL, delivered=True)


def _transient() -> DeliveryReceipt:
    return DeliveryReceipt(channel=DeliveryChannelKind.EMAIL, delivered=False, retryable=True)


def _permanent() -> DeliveryReceipt:
    return DeliveryReceipt(channel=DeliveryChannelKind.EMAIL, delivered=False, retryable=False)


def _worker(session_factory, channel: _ScriptedChannel, *, leader=None) -> SchedulerWorker:
    return SchedulerWorker(
        SchedulerContext(
            session_factory=session_factory,
            orchestrator=None,  # type: ignore[arg-type]
            router=None,  # type: ignore[arg-type]
            channels={
                DeliveryChannelKind.IN_APP: InAppChannel(),
                DeliveryChannelKind.EMAIL: channel,
            },  # type: ignore[dict-item]
            entitlement_resolver=EntitlementResolver(),
            leader=leader or AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )


async def _deliver(worker: SchedulerWorker, session_factory, user_id: str) -> None:
    async with session_factory() as session:
        user = await SqlAlchemyUserStore(session).get(user_id=user_id)
        await worker._deliver(
            SqlAlchemySchedulerStore(session),
            user=user,
            source=NotificationSource.ALERT,
            source_id="rule-1",
            channels=(DeliveryChannelKind.EMAIL,),
            title="2330 crossed",
            body="above 1000",
            trigger_date=date(2026, 10, 13),
            deep_link={"route": "/stock/2330"},
        )
        await session.commit()


async def _email_row(session_factory) -> NotificationRow:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(NotificationRow).where(NotificationRow.channel == "email")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    return rows[0]


async def test_a_delivered_notification_is_marked_sent(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    channel = _ScriptedChannel(_ok())

    await _deliver(_worker(session_factory, channel), session_factory, user)

    row = await _email_row(session_factory)
    assert (row.delivery_state, row.delivery_attempts, row.next_attempt_at) == ("sent", 1, None)
    assert len(channel.sent) == 1


async def test_a_transient_failure_is_kept_for_retry_not_lost(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    channel = _ScriptedChannel(_transient())

    await _deliver(_worker(session_factory, channel), session_factory, user)

    row = await _email_row(session_factory)
    assert row.delivery_state == "pending"
    assert row.delivery_attempts == 1
    assert row.next_attempt_at is not None


async def test_a_send_that_raises_is_a_transient_failure(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    channel = _ScriptedChannel(RuntimeError("smtp down"))

    await _deliver(_worker(session_factory, channel), session_factory, user)

    assert (await _email_row(session_factory)).delivery_state == "pending"


async def test_a_permanent_failure_is_final(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)

    await _deliver(_worker(session_factory, _ScriptedChannel(_permanent())), session_factory, user)

    assert (await _email_row(session_factory)).delivery_state == "failed"


async def test_the_retry_sweep_delivers_a_pending_notification_once(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    channel = _ScriptedChannel(_transient(), _ok())
    worker = _worker(session_factory, channel)
    await _deliver(worker, session_factory, user)

    assert await worker.retry_pending_deliveries() == 0  # the retry time has not come yet
    later = datetime.now(UTC) + timedelta(hours=1)
    assert await worker.retry_pending_deliveries(now=later) == 1
    assert await worker.retry_pending_deliveries(now=later) == 0  # and it is not sent again

    row = await _email_row(session_factory)
    assert (row.delivery_state, row.delivery_attempts) == ("sent", 2)
    assert len(channel.sent) == 2
    assert len(set(channel.sent)) == 1  # the same notification, retried


async def test_a_notification_stays_pending_until_its_attempts_run_out(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    channel = _ScriptedChannel(*[_transient()] * 10)
    worker = _worker(session_factory, channel)
    await _deliver(worker, session_factory, user)

    when = datetime.now(UTC)
    for _ in range(10):
        when += timedelta(days=1)
        await worker.retry_pending_deliveries(now=when)

    row = await _email_row(session_factory)
    assert row.delivery_state == "failed"
    assert row.delivery_attempts == 6
    assert len(channel.sent) == 6  # then it stops


async def test_a_row_recorded_but_never_sent_is_picked_up(session_factory) -> None:
    """The process died between the commit and the send: a pending row with no attempt time."""
    user = await insert_user(session_factory, tier=Tier.PRO)
    channel = _ScriptedChannel(_ok())
    worker = _worker(session_factory, channel)
    async with session_factory() as session:
        await SqlAlchemySchedulerStore(session).record_notification(
            user_id=user,
            source=NotificationSource.ALERT,
            source_id="rule-1",
            channel=DeliveryChannelKind.EMAIL,
            trigger_date=date(2026, 10, 13),
            payload={"title": "t", "body": "b", "deep_link": {}},
            delivery_state="pending",
        )
        await session.commit()

    assert await worker.retry_pending_deliveries() == 0  # too fresh: the original send may be live
    assert await worker.retry_pending_deliveries(now=datetime.now(UTC) + timedelta(hours=1)) == 1
    assert (await _email_row(session_factory)).delivery_state == "sent"


async def test_a_standby_scheduler_does_not_retry(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    channel = _ScriptedChannel(_transient())
    await _deliver(_worker(session_factory, channel), session_factory, user)

    standby = _worker(session_factory, channel, leader=NeverLeader())
    later = datetime.now(UTC) + timedelta(hours=1)

    assert await standby.retry_pending_deliveries(now=later) == 0
    assert len(channel.sent) == 1


async def test_the_row_is_committed_before_the_send(session_factory) -> None:
    """If the process dies inside `send`, the notification must already be durable."""
    user = await insert_user(session_factory, tier=Tier.PRO)
    seen: list[str] = []

    class _Probe(_ScriptedChannel):
        async def send(self, notification, recipient):  # type: ignore[no-untyped-def]
            row = await _email_row(session_factory)  # a different session: only committed data
            seen.append(row.delivery_state)
            return _ok()

    await _deliver(_worker(session_factory, _Probe()), session_factory, user)

    assert seen == ["pending"]


async def test_a_second_delivery_of_the_same_alert_sends_nothing(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    channel = _ScriptedChannel(_ok(), _ok())
    worker = _worker(session_factory, channel)

    await _deliver(worker, session_factory, user)
    await _deliver(worker, session_factory, user)

    assert len(channel.sent) == 1


class _ScriptedTelegram(_ScriptedChannel):
    kind = DeliveryChannelKind.TELEGRAM


def _telegram_worker(session_factory, channel: _ScriptedTelegram) -> SchedulerWorker:
    return SchedulerWorker(
        SchedulerContext(
            session_factory=session_factory,
            orchestrator=None,  # type: ignore[arg-type]
            router=None,  # type: ignore[arg-type]
            channels={
                DeliveryChannelKind.IN_APP: InAppChannel(),
                DeliveryChannelKind.TELEGRAM: channel,
            },  # type: ignore[dict-item]
            entitlement_resolver=EntitlementResolver(),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )


def _telegram_receipt(*, delivered: bool, retryable: bool, gone: bool) -> DeliveryReceipt:
    return DeliveryReceipt(
        channel=DeliveryChannelKind.TELEGRAM,
        delivered=delivered,
        retryable=retryable,
        address_gone=gone,
    )


async def _link_telegram(session_factory, user_id: str) -> None:
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        await store.start_channel_link(
            user_id=user_id,
            channel="telegram",
            token="tok",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        await store.complete_channel_link(
            channel="telegram", token="tok", address="555", now=datetime.now(UTC)
        )
        await session.commit()


async def _telegram_address(session_factory, user_id: str) -> str | None:
    async with session_factory() as session:
        return await SqlAlchemySchedulerStore(session).channel_address(
            user_id=user_id, channel="telegram"
        )


async def _deliver_telegram(
    worker: SchedulerWorker, session_factory, user_id: str, *, source_id: str = "rule-1"
) -> None:
    async with session_factory() as session:
        user = await SqlAlchemyUserStore(session).get(user_id=user_id)
        await worker._deliver(
            SqlAlchemySchedulerStore(session),
            user=user,
            source=NotificationSource.ALERT,
            source_id=source_id,
            channels=(DeliveryChannelKind.TELEGRAM,),
            title="2330 crossed",
            body="above 1000",
            trigger_date=date(2026, 10, 13),
            deep_link={"route": "/stock/2330"},
        )
        await session.commit()


async def test_a_gone_telegram_chat_is_unlinked_so_settings_stops_claiming_it_works(
    session_factory,
) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _link_telegram(session_factory, user)
    gone = _telegram_receipt(delivered=False, retryable=False, gone=True)

    await _deliver_telegram(
        _telegram_worker(session_factory, _ScriptedTelegram(gone)), session_factory, user
    )

    assert await _telegram_address(session_factory, user) is None


async def test_a_transient_telegram_failure_keeps_the_chat_linked(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _link_telegram(session_factory, user)
    busy = _telegram_receipt(delivered=False, retryable=True, gone=False)
    permanent = _telegram_receipt(delivered=False, retryable=False, gone=False)

    for rule, outcome in enumerate((busy, permanent, RuntimeError("down"))):
        worker = _telegram_worker(session_factory, _ScriptedTelegram(outcome))
        await _deliver_telegram(worker, session_factory, user, source_id=f"rule-{rule}")
        assert await _telegram_address(session_factory, user) == "555"


async def test_a_gone_chat_found_by_the_retry_sweep_is_unlinked_too(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _link_telegram(session_factory, user)
    busy = _telegram_receipt(delivered=False, retryable=True, gone=False)
    gone = _telegram_receipt(delivered=False, retryable=False, gone=True)
    worker = _telegram_worker(session_factory, _ScriptedTelegram(busy, gone))

    await _deliver_telegram(worker, session_factory, user)
    assert await _telegram_address(session_factory, user) == "555"
    await worker.retry_pending_deliveries(now=datetime.now(UTC) + timedelta(hours=1))

    assert await _telegram_address(session_factory, user) is None


class _RecipientProbe(_ScriptedTelegram):
    def __init__(self) -> None:
        super().__init__(_ok())
        self.recipients: list[DeliveryRecipient] = []

    async def send(self, notification, recipient):  # type: ignore[no-untyped-def]
        self.recipients.append(recipient)
        return await super().send(notification, recipient)


async def _app_url_seen_by_telegram(session_factory, app_base_url: str | None) -> str | None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    await _link_telegram(session_factory, user)
    probe = _RecipientProbe()
    worker = SchedulerWorker(
        SchedulerContext(
            session_factory=session_factory,
            orchestrator=None,  # type: ignore[arg-type]
            router=None,  # type: ignore[arg-type]
            channels={DeliveryChannelKind.TELEGRAM: probe},  # type: ignore[dict-item]
            entitlement_resolver=EntitlementResolver(),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
            app_base_url=app_base_url,
        )
    )
    await _deliver_telegram(worker, session_factory, user)
    return probe.recipients[0].app_url


async def test_the_recipient_carries_the_app_url_only_when_it_is_https(session_factory) -> None:
    assert await _app_url_seen_by_telegram(session_factory, "https://miru.example/") == (
        "https://miru.example"
    )
    assert await _app_url_seen_by_telegram(session_factory, "http://localhost:3000") is None
    assert await _app_url_seen_by_telegram(session_factory, None) is None
