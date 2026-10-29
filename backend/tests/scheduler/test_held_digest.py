"""What quiet hours parked goes out as one laid-out digest per channel: a heading with the count,
one line per title (newest last, capped like the morning recap), the footer. The digest row is
stored with its layout, so a retry resends the same layout."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from accounts.hooks import EntitlementResolver
from db.models import NotificationRow
from governance.textscan import check_output
from ingest.leader import AlwaysLeader
from scheduler.delivery.base import DeliveryNotification, DeliveryReceipt, DeliveryRecipient
from scheduler.delivery.channels import InAppChannel
from scheduler.delivery.layout import TRIM_POINTER, fit_html, fit_text, render_text
from scheduler.models import DeliveryChannelKind, NotificationSource
from scheduler.notification_messages import DIGEST_MAX_LINES, held_layout
from scheduler.quota import AllowAllQuotaGate
from scheduler.store import SqlAlchemySchedulerStore
from scheduler.worker import SchedulerContext, SchedulerWorker
from schemas.alerts import NON_ADVICE_FOOTER
from schemas.enums import Tier
from tests.scheduler.conftest import insert_user


class _Telegram:
    kind = DeliveryChannelKind.TELEGRAM

    def __init__(self, *, fail_first: bool = False) -> None:
        self.sent: list[DeliveryNotification] = []
        self._fail_first = fail_first

    async def send(
        self, notification: DeliveryNotification, recipient: DeliveryRecipient
    ) -> DeliveryReceipt:
        del recipient
        self.sent.append(notification)
        if self._fail_first and len(self.sent) == 1:
            return DeliveryReceipt(channel=self.kind, delivered=False, retryable=True)
        return DeliveryReceipt(channel=self.kind, delivered=True)


def _worker(session_factory, channel: _Telegram) -> SchedulerWorker:
    return SchedulerWorker(
        SchedulerContext(
            session_factory=session_factory,
            orchestrator=None,  # type: ignore[arg-type]
            router=None,  # type: ignore[arg-type]
            channels={DeliveryChannelKind.IN_APP: InAppChannel(), channel.kind: channel},  # type: ignore[dict-item]
            entitlement_resolver=EntitlementResolver(),
            leader=AlwaysLeader(),
            quota_gate=AllowAllQuotaGate(),
        )
    )


async def _park(session_factory, worker: SchedulerWorker, user: str, count: int) -> list[str]:
    titles = [f"2330 - 規則 {n}" for n in range(1, count + 1)]
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        loaded = await worker._load_user(session, user)
        assert loaded is not None
        for n, title in enumerate(titles, start=1):
            await worker._deliver(
                store,
                user=loaded,
                source=NotificationSource.ALERT,
                source_id=f"rule-{n}",
                channels=(DeliveryChannelKind.TELEGRAM,),
                title=title,
                body=f"條件 {n} 已符合\n{NON_ADVICE_FOOTER}",
                trigger_date=date(2026, 10, 5),
                deep_link={},
                hold_external=True,
            )
        await session.commit()
        # Rows written in one tight loop can share a clock tick; give each its own second.
        moment = datetime.now(UTC) - timedelta(minutes=5)
        for n in range(1, count + 1):
            row = (
                (
                    await session.execute(
                        select(NotificationRow).where(NotificationRow.source_id == f"rule-{n}")
                    )
                )
                .scalars()
                .all()
            )
            for found in row:
                found.delivered_at = moment + timedelta(seconds=n)
        await session.commit()
    return titles


async def _digest_rows(session_factory) -> list[NotificationRow]:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(NotificationRow).where(NotificationRow.source == "digest")
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


def _sent_text(notification: DeliveryNotification) -> str:
    assert notification.layout is not None
    return render_text(notification.layout, NON_ADVICE_FOOTER)


@pytest.mark.parametrize("count", [1, 3])
async def test_parked_notifications_are_sent_as_one_laid_out_digest(
    session_factory, count: int
) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    channel = _Telegram()
    worker = _worker(session_factory, channel)
    titles = await _park(session_factory, worker, user, count)

    assert await worker.release_held() == count

    (sent,) = channel.sent
    assert sent.layout is not None
    block = sent.layout.blocks[0]
    assert block.head == f"🔕 靜音期間的通知（{count} 則）"
    assert block.lines == tuple(f"• {t}" for t in titles)  # newest last
    text = _sent_text(sent)
    assert text.endswith(NON_ADVICE_FOOTER)
    assert check_output(text, has_market_data=False).reason is None
    assert sent.body.count(NON_ADVICE_FOOTER) == 1  # the plain body keeps its one footer
    assert all(f"條件 {n} 已符合" in sent.body for n in range(1, count + 1))
    (row,) = await _digest_rows(session_factory)
    assert row.delivery_state == "sent"
    assert row.payload["layout"]["blocks"][0]["head"] == block.head


async def test_more_held_than_the_cap_keeps_the_newest_and_counts_the_rest(
    session_factory,
) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    channel = _Telegram()
    worker = _worker(session_factory, channel)
    total = DIGEST_MAX_LINES + 3
    titles = await _park(session_factory, worker, user, total)

    await worker.release_held()

    layout = channel.sent[0].layout
    assert layout is not None
    lines = layout.blocks[0].lines
    assert layout.blocks[0].head == f"🔕 靜音期間的通知（{total} 則）"
    assert lines[:-1] == tuple(f"• {t}" for t in titles[-DIGEST_MAX_LINES:])
    assert lines[-1] == "…另有 3 則較早的通知，請見通知紀錄"


async def test_a_retry_resends_the_same_layout(session_factory) -> None:
    user = await insert_user(session_factory, tier=Tier.PRO)
    channel = _Telegram(fail_first=True)
    worker = _worker(session_factory, channel)
    await _park(session_factory, worker, user, 3)

    await worker.release_held()
    (row,) = await _digest_rows(session_factory)
    assert row.delivery_state == "pending"

    assert await worker.retry_pending_deliveries(now=datetime.now(UTC) + timedelta(hours=1)) == 1

    first, second = channel.sent
    assert first.layout is not None
    assert second.layout == first.layout
    assert second.notification_id == first.notification_id
    assert await worker.release_held() == 0  # nothing is released twice


def test_the_rendered_samples_fit_both_chat_channels() -> None:
    layout = held_layout(["2330 - 規則 1", "2317 - 規則 2"])
    html_text = fit_html(layout, NON_ADVICE_FOOTER, pointer=TRIM_POINTER)
    line_text = fit_text(layout, NON_ADVICE_FOOTER, pointer=TRIM_POINTER)
    assert html_text is not None
    assert html_text.startswith(
        "<b>🔕 靜音期間的通知（2 則）</b>\n• 2330 - 規則 1\n• 2317 - 規則 2"
    )
    assert line_text is not None
    assert line_text.startswith("🔕 靜音期間的通知（2 則）\n• 2330 - 規則 1\n• 2317 - 規則 2")
