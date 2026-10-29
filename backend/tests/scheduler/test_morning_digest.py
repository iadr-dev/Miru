"""晨間摘要: one deterministic recap per local trading day, in the user's timezone, only if they
opted in and there is something to say."""

from __future__ import annotations

from datetime import UTC, date, datetime

from scheduler.models import (
    DeliveryChannelKind,
    NotificationSource,
    QuietHours,
    ScheduleAction,
    ScheduleTrigger,
    TriggerType,
)
from scheduler.quota import DenyAllQuotaGate
from scheduler.store import SqlAlchemySchedulerStore
from schemas.alerts import NON_ADVICE_FOOTER
from schemas.enums import DataClass, Tier
from tests.scheduler.conftest import insert_user
from tests.scheduler.test_quiet_hours import _RecordingPush, _worker
from tools.morning_brief_models import GetMorningBriefOutput, MorningBriefInput


def _recap_worker(session_factory, push=None):
    """No chat quota, so the AI brief is skipped and the plain recap is what gets sent."""
    return _worker(session_factory, push, quota_gate=DenyAllQuotaGate())


# Monday 2026-10-05, 08:30 in Taipei.
_MONDAY_0830_TPE = datetime(2026, 10, 5, 0, 30, tzinfo=UTC)


async def _user(
    session_factory,
    *,
    digest: bool = True,
    quiet: QuietHours | None = None,
    timezone: str = "Asia/Taipei",
    with_job: bool = True,
) -> str:
    user = await insert_user(session_factory, tier=Tier.PRO)
    async with session_factory() as session:
        store = SqlAlchemySchedulerStore(session)
        await store.upsert_preferences(
            user_id=user,
            channels=(DeliveryChannelKind.IN_APP, DeliveryChannelKind.PUSH),
            enabled_categories=(),
            quiet_hours=quiet,
            morning_digest=digest,
            timezone=timezone,
        )
        if with_job:
            await store.create_job(
                user_id=user,
                trigger=ScheduleTrigger(type=TriggerType.MARKET_EVENT, spec="after_close"),
                action=ScheduleAction(
                    skill_ref="daily-context-brief", inputs={"stock_ids": ["2330"]}
                ),
                delivery_channels=(DeliveryChannelKind.IN_APP,),
                next_run=datetime(2026, 10, 6, tzinfo=UTC),
            )
        await session.commit()
    return user


async def _overnight_alert(session_factory, user: str) -> None:
    async with session_factory() as session:
        await SqlAlchemySchedulerStore(session).record_notification(
            user_id=user,
            source=NotificationSource.ALERT,
            source_id="rule-1",
            channel=DeliveryChannelKind.IN_APP,
            trigger_date=date(2026, 10, 4),
            payload={"title": "2330 - 收盤價高於 600", "body": "條件已達成"},
        )
        await session.commit()


async def _inbox(session_factory, user: str):
    async with session_factory() as session:
        return await SqlAlchemySchedulerStore(session).list_notifications(user_id=user)


async def test_the_digest_recaps_overnight_notifications_and_active_counts(session_factory) -> None:
    user = await _user(session_factory)
    await _overnight_alert(session_factory, user)
    push = _RecordingPush()

    sent = await _recap_worker(session_factory, push).send_morning_digests(now=_MONDAY_0830_TPE)

    assert sent == 1
    digest = next(
        n for n in await _inbox(session_factory, user) if n.source is NotificationSource.DIGEST
    )
    body = str(digest.payload["body"])
    assert "2330 - 收盤價高於 600" in body
    assert "0 個提醒、1 個排程" in body
    assert body.count(NON_ADVICE_FOOTER) == 1
    assert str(digest.payload["title"]) == "晨間摘要 2026-10-05"
    assert len(push.sent) == 1  # and it reached the browser channel too


async def test_it_is_sent_once_per_day(session_factory) -> None:
    user = await _user(session_factory)
    await _overnight_alert(session_factory, user)
    worker = _recap_worker(session_factory, _RecordingPush())

    assert await worker.send_morning_digests(now=_MONDAY_0830_TPE) == 1
    again = await worker.send_morning_digests(now=_MONDAY_0830_TPE.replace(minute=45))
    digests = [
        n
        for n in await _inbox(session_factory, user)
        if n.source is NotificationSource.DIGEST and n.channel is DeliveryChannelKind.IN_APP
    ]
    assert len(digests) == 1
    assert again == 0  # already sent today


async def test_it_respects_the_opt_in_the_window_and_market_holidays(session_factory) -> None:
    off = await _user(session_factory, digest=False)
    await _overnight_alert(session_factory, off)
    worker = _recap_worker(session_factory)
    assert await worker.send_morning_digests(now=_MONDAY_0830_TPE) == 0  # not opted in

    await _user(session_factory)
    before = datetime(2026, 10, 4, 23, 30, tzinfo=UTC)  # 07:30 Monday in Taipei
    after = datetime(2026, 10, 5, 4, 30, tzinfo=UTC)  # 12:30 Monday in Taipei
    assert await worker.send_morning_digests(now=before) == 0
    assert await worker.send_morning_digests(now=after) == 0
    holiday = datetime(2026, 10, 9, 0, 30, tzinfo=UTC)  # 國慶日補假, 08:30 Taipei
    assert await worker.send_morning_digests(now=holiday) == 0
    saturday = datetime(2026, 10, 10, 0, 30, tzinfo=UTC)
    assert await worker.send_morning_digests(now=saturday) == 0


async def test_it_uses_the_users_timezone(session_factory) -> None:
    await _user(session_factory, timezone="America/New_York")
    worker = _recap_worker(session_factory)
    # 00:30 UTC Monday is 20:30 Sunday in New York: not yet morning there.
    assert await worker.send_morning_digests(now=_MONDAY_0830_TPE) == 0
    # 12:30 UTC Monday is 08:30 in New York.
    assert await worker.send_morning_digests(now=datetime(2026, 10, 5, 12, 30, tzinfo=UTC)) == 1


async def test_it_waits_out_quiet_hours_then_still_goes_before_noon(session_factory) -> None:
    await _user(session_factory, quiet=QuietHours(start="22:00", end="09:30"))
    worker = _recap_worker(session_factory)
    assert await worker.send_morning_digests(now=_MONDAY_0830_TPE) == 0  # 08:30, still quiet
    assert await worker.send_morning_digests(now=_MONDAY_0830_TPE.replace(hour=1, minute=45)) == 1


async def test_nothing_to_report_sends_nothing(session_factory) -> None:
    await _user(session_factory, with_job=False)  # no overnight items, no alerts, no schedules
    assert await _recap_worker(session_factory).send_morning_digests(now=_MONDAY_0830_TPE) == 0


_REPORT = chr(10).join(
    [
        "**【晨間摘要】2026-10-05（星期一）08:30 台北**",
        "",
        "### Top 3 Drivers 三大重點",
        "",
        "1. 費城半導體指數 +2.4%（13,136.67） → 半導體產業的美股參考指數",
        "2. 台積電 ADR（TSM） +3.0%（472.78） → 台積電的存託憑證",
        "3. VIX -6.5%（15.31） → 選擇權隱含波動",
        "",
        "### 台股 Pre-Open",
        "",
        "| 表 |",
    ]
)


class _BriefTool:
    """Stands in for `get_morning_brief`: records the symbols it was asked about."""

    name = "get_morning_brief"
    data_class = DataClass.PUBLIC
    supports_row_preview = False

    def __init__(self, *, boom: bool = False) -> None:
        self.asked: list[MorningBriefInput] = []
        self._boom = boom

    async def run(self, args, ctx):
        del ctx
        self.asked.append(args)
        if self._boom:
            raise RuntimeError("source down")
        return GetMorningBriefOutput(
            date_taipei="2026-10-05",
            time_taipei="08:30",
            report=_REPORT,
        )


async def _watchlist(session_factory, user: str) -> None:
    from db.models import WatchlistItemRow, WatchlistRow  # noqa: PLC0415 — test-local

    now = datetime(2026, 10, 1, tzinfo=UTC)
    async with session_factory() as session:
        session.add(
            WatchlistRow(
                id="wl",
                user_id=user,
                name="自選",
                market="TW",
                asset_class="stock",
                sort_order="a",
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        for i, (symbol, market, asset_class) in enumerate(
            [("2330", "TW", "stock"), ("NVDA", "US", "stock"), ("00403A", "TW", "etf")]
        ):
            session.add(
                WatchlistItemRow(
                    id=f"i{i}",
                    watchlist_id="wl",
                    user_id=user,
                    symbol=symbol,
                    market=market,
                    asset_class=asset_class,
                    sort_order=str(i),
                    added_at=now,
                )
            )
        await session.commit()


async def test_the_brief_covers_the_users_watchlist_and_lands_in_one_thread(
    session_factory,
) -> None:
    user = await _user(session_factory)
    await _watchlist(session_factory, user)
    tool = _BriefTool()
    push = _RecordingPush()
    worker = _worker(session_factory, push, brief_tool=tool)

    assert await worker.send_morning_digests(now=_MONDAY_0830_TPE) == 1

    asked = tool.asked[0]
    assert asked.tw_stocks == ("2330",)
    assert asked.us_stocks == ("NVDA",)
    assert asked.etfs == ("00403A",)
    inbox = await _inbox(session_factory, user)
    digest = next(n for n in inbox if n.source is NotificationSource.DIGEST)
    assert digest.payload["deep_link"]["route"] == "thread"  # type: ignore[index]
    thread_id = digest.payload["deep_link"]["thread_id"]  # type: ignore[index]
    # The notification is the headline plus the top drivers, not the whole report.
    body = str(digest.payload["body"])
    assert body.startswith("【晨間摘要】2026-10-05")
    assert "費城半導體指數 +2.4%" in body
    assert "| 表 |" not in body
    assert len(push.sent) == 1
    assert push.sent[0].body.count(NON_ADVICE_FOOTER) == 1

    # The next trading day's brief is appended to the same thread, not a new one.
    assert await worker.send_morning_digests(now=_MONDAY_0830_TPE.replace(day=6)) == 1
    async with session_factory() as session:
        prefs = await SqlAlchemySchedulerStore(session).get_preferences(user_id=user)
    assert prefs is not None
    assert prefs.digest_thread_id == thread_id


async def test_the_brief_costs_no_chat_quota(session_factory) -> None:
    user = await _user(session_factory)
    worker = _worker(session_factory, brief_tool=_BriefTool(), quota_gate=DenyAllQuotaGate())
    assert await worker.send_morning_digests(now=_MONDAY_0830_TPE) == 1
    inbox = await _inbox(session_factory, user)
    digest = next(n for n in inbox if n.source is NotificationSource.DIGEST)
    assert digest.payload["deep_link"]["route"] == "thread"  # type: ignore[index]


async def test_when_the_brief_tool_fails_the_plain_recap_is_sent_instead(session_factory) -> None:
    user = await _user(session_factory)
    await _overnight_alert(session_factory, user)
    worker = _worker(session_factory, brief_tool=_BriefTool(boom=True))

    assert await worker.send_morning_digests(now=_MONDAY_0830_TPE) == 1
    inbox = await _inbox(session_factory, user)
    digest = next(n for n in inbox if n.source is NotificationSource.DIGEST)
    assert "過去 24 小時共 1 則通知" in str(digest.payload["body"])


async def test_the_recap_and_the_brief_hand_their_layout_to_the_channels(session_factory) -> None:
    user = await _user(session_factory)
    await _overnight_alert(session_factory, user)
    recap_push = _RecordingPush()
    await _recap_worker(session_factory, recap_push).send_morning_digests(now=_MONDAY_0830_TPE)
    recap = recap_push.sent[0].layout
    assert recap is not None
    assert recap.blocks[0].head == "🗒 過去 24 小時 1 則通知"
    assert recap.blocks[0].lines == ("• 2330 - 收盤價高於 600",)
    assert recap.blocks[1].lines == ("目前啟用中：0 個提醒、1 個排程",)

    await _user(session_factory)
    brief_push = _RecordingPush()
    worker = _worker(session_factory, brief_push, brief_tool=_BriefTool())
    await worker.send_morning_digests(now=_MONDAY_0830_TPE)
    brief = brief_push.sent[-1].layout
    assert brief is not None
    assert brief.blocks[0].head == "🌅 晨間摘要 2026-10-05（星期一）"
    assert brief.blocks[0].lines[0] == "• 費城半導體指數 +2.4%（13,136.67）"
    assert brief.blocks[-1].lines == ("完整報告請見 Miru",)
