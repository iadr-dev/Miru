"""The user-scoped reads behind the `private` account tools (`tools.account_tools`).

`tools/` cannot import the stores that hold a user's watchlists, alerts, schedules, usage and
notifications, so it declares the `AccountDataReader` Protocol and this module implements it over
the SAME stores the REST routes use - `WatchlistStore`, `SchedulerStore`,
`StrategySubscriptionStore`, the usage store and `summarize_llm_usage`. No query is written here;
this layer only picks fields and shapes them for the model.

Isolation: every read takes the user id from the tool (which took it from the `ToolContext`), the
stores filter on it, and the call runs bound to that user for row-level security (`db.rls`), so a
bug in a store's `WHERE` still cannot return another user's rows on Postgres. Each read uses its own
short session and never commits.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accounts.byok import byok_active
from accounts.entitlements import TIER_LIMITS
from config.settings import Settings
from db import rls
from memory.watchlists import WatchlistStore
from scheduler.models import (
    AlertRule,
    JobStatus,
    Notification,
    ScheduleJob,
    StrategySubscription,
)
from scheduler.store import SchedulerStore, SqlAlchemySchedulerStore
from scheduler.strategy_store import StrategySubscriptionStore
from schemas.alerts import describe
from schemas.enums import Tier, UserMarket
from schemas.strategy_watch import STRATEGY_LABEL
from tools.account_tools import (
    MAX_NOTIFICATION_BODY,
    MAX_USAGE_MODELS,
    AlertRuleSummary,
    AlertStatus,
    AutomationsSnapshot,
    ChatQuota,
    LlmUsageToday,
    ModelUsageToday,
    NotificationSummary,
    ScheduleStatus,
    ScheduleSummary,
    StrategyWatchSummary,
    UsageSnapshot,
    WatchlistEntry,
    WatchlistGroupSummary,
    WatchlistsSnapshot,
)
from usage.llm_usage import summarize_llm_usage
from usage.store import UsageStore

logger = structlog.get_logger(__name__)

_TODAY_ONLY: Final = 1


def _aware(moment: datetime) -> datetime:
    """SQLite hands datetimes back naive (stored as UTC); Postgres hands them back aware."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def _alert_status(rule: AlertRule, now: datetime) -> tuple[AlertStatus, str | None]:
    if rule.expires_at is not None and _aware(rule.expires_at) <= now:
        return "expired", None
    if not rule.enabled:
        if rule.auto_paused_reason:
            return "auto_paused", rule.auto_paused_reason
        return "paused", None
    return "active", None


def _job_status(job: ScheduleJob) -> tuple[ScheduleStatus, str | None]:
    if not job.enabled:
        return "paused", None
    if job.status is JobStatus.ERROR:
        return "error", job.status_reason
    if job.status is JobStatus.SKIPPED_QUOTA:
        return "skipped_quota", job.status_reason
    return "active", None


def _clip(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def _payload_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _notification_summary(row: Notification) -> NotificationSummary:
    # Only the display fields: never the deep link, ids or the dedupe key.
    source = row.source.value
    return NotificationSummary(
        title=_clip(_payload_text(row.payload, "title"), 120),
        body=_clip(_payload_text(row.payload, "body"), MAX_NOTIFICATION_BODY),
        source=source,
        delivered_at=_aware(row.delivered_at),
        read=row.read_at is not None,
    )


class SqlAccountDataReader:
    """`AccountDataReader` over the app database. Built once per process from the session factory;
    `usage_store` and `settings` are optional so a narrow wiring still works (no usage store means
    today's chat count reads as 0 - never a made-up number, the tool says `used_today=0`)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        usage_store: UsageStore,
        settings: Settings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._usage_store = usage_store
        self._settings = settings
        self._clock = clock

    @staticmethod
    @contextlib.contextmanager
    def _scope(user_id: str) -> Generator[None]:
        """Run the block's queries as `user_id` for row-level security. In a request the same user
        is already bound; a different one would be a wiring bug, so refuse rather than re-bind."""
        bound = rls.current_user_id()
        if bound is not None and bound != user_id:
            raise PermissionError("the acting user does not match the tool context's user")
        with rls.acting_as(user_id):
            yield

    async def watchlists(
        self, *, user_id: str, market: UserMarket | None, max_items: int
    ) -> WatchlistsSnapshot:
        with self._scope(user_id):
            return await self._watchlists(user_id, market, max_items)

    async def _watchlists(
        self, user_id: str, market: UserMarket | None, max_items: int
    ) -> WatchlistsSnapshot:
        async with self._session_factory() as session:
            store = WatchlistStore(session)
            groups = [g for g in await store.list_groups(user_id) if market in (None, g.market)]
            items_by_group = await store.items_by_group(user_id)
        remaining = max_items
        total = 0
        views: list[WatchlistGroupSummary] = []
        for group in groups:
            items = items_by_group.get(group.id, [])
            total += len(items)
            kept = items[: max(remaining, 0)]
            remaining -= len(kept)
            views.append(
                WatchlistGroupSummary(
                    name=group.name,
                    market=group.market,
                    asset_class=group.asset_class,
                    is_default=group.is_default,
                    item_count=len(items),
                    items=tuple(WatchlistEntry(symbol=i.symbol, market=i.market) for i in kept),
                )
            )
        return WatchlistsSnapshot(
            groups=tuple(views), total_items=total, truncated=total > max_items
        )

    async def automations(
        self, *, user_id: str, max_alerts: int, max_schedules: int
    ) -> AutomationsSnapshot:
        with self._scope(user_id):
            return await self._automations(user_id, max_alerts, max_schedules)

    async def _automations(
        self, user_id: str, max_alerts: int, max_schedules: int
    ) -> AutomationsSnapshot:
        now = self._clock()
        async with self._session_factory() as session:
            store: SchedulerStore = SqlAlchemySchedulerStore(session)
            alerts = await store.list_alerts(user_id=user_id)
            jobs = await store.list_jobs(user_id=user_id)
            subscription = await StrategySubscriptionStore(session).get(user_id=user_id)
            names = {g.id: g.name for g in await WatchlistStore(session).list_groups(user_id)}
        return AutomationsSnapshot(
            alerts=tuple(self._alert(rule, names, now) for rule in alerts[:max_alerts]),
            alert_count=len(alerts),
            schedules=tuple(self._schedule(job) for job in jobs[:max_schedules]),
            schedule_count=len(jobs),
            strategy_watch=self._strategy(subscription, names) if subscription else None,
        )

    @staticmethod
    def _alert(rule: AlertRule, names: dict[str, str], now: datetime) -> AlertRuleSummary:
        status, reason = _alert_status(rule, now)
        if rule.symbol is not None and rule.market is not None:
            target = f"{rule.symbol} {rule.market.value}"
        else:
            target = f"watchlist group {names.get(rule.watchlist_id or '', '(deleted group)')!r}"
        return AlertRuleSummary(
            target=target,
            condition=describe(rule.condition),
            status=status,
            status_reason=reason,
            trigger_mode=rule.trigger_mode.value,
            last_triggered=_aware(rule.last_triggered) if rule.last_triggered else None,
            expires_at=_aware(rule.expires_at) if rule.expires_at else None,
        )

    @staticmethod
    def _schedule(job: ScheduleJob) -> ScheduleSummary:
        status, reason = _job_status(job)
        return ScheduleSummary(
            briefing=job.action.skill_ref,
            cadence=f"{job.trigger.type.value} {job.trigger.spec} ({job.trigger.market.value})",
            status=status,
            status_reason=reason,
            last_run=_aware(job.last_run) if job.last_run else None,
            next_run=_aware(job.next_run) if job.next_run and job.enabled else None,
        )

    @staticmethod
    def _strategy(sub: StrategySubscription, names: dict[str, str]) -> StrategyWatchSummary:
        scope = (
            "every stock on today's list"
            if sub.watchlist_id is None
            else f"watchlist group {names.get(sub.watchlist_id, '(deleted group)')!r}"
        )
        return StrategyWatchSummary(
            enabled=sub.enabled,
            strategies=tuple(STRATEGY_LABEL.get(key, key) for key in sub.strategies),
            scope=scope,
            morning_list=sub.morning_list,
            daily_limit=sub.daily_limit,
        )

    async def usage(self, *, user_id: str, tier: Tier, include_llm: bool) -> UsageSnapshot:
        with self._scope(user_id):
            return await self._usage(user_id, tier, include_llm)

    async def _usage(self, user_id: str, tier: Tier, include_llm: bool) -> UsageSnapshot:
        counts = await self._usage_store.today_counts(user_id=user_id)
        async with self._session_factory() as session:
            own_key = await byok_active(session, self._settings, user_id=user_id)
            llm = (
                await summarize_llm_usage(session, user_id=user_id, days=_TODAY_ONLY)
                if (include_llm)
                else None
            )
        tier_limit = TIER_LIMITS[tier].chat_limit_per_day
        limit = None if own_key else tier_limit
        used = counts.get("chats", 0)
        chat = ChatQuota(
            used_today=used,
            limit_per_day=limit,
            remaining_today=None if limit is None else max(limit - used, 0),
            unlimited_because=("own_key" if own_key else "tier") if limit is None else None,
        )
        if llm is None:
            return UsageSnapshot(chat=chat)
        top = sorted(llm.models, key=lambda m: m.input_tokens + m.output_tokens, reverse=True)
        return UsageSnapshot(
            chat=chat,
            llm_today=LlmUsageToday(
                calls=llm.totals.calls,
                input_tokens=llm.totals.input_tokens,
                output_tokens=llm.totals.output_tokens,
                top_models=tuple(
                    ModelUsageToday(
                        model=f"{m.provider}/{m.model}",
                        calls=m.calls,
                        tokens=m.input_tokens + m.output_tokens,
                    )
                    for m in top[:MAX_USAGE_MODELS]
                ),
            ),
        )

    async def notifications(
        self, *, user_id: str, limit: int, unread_only: bool
    ) -> list[NotificationSummary]:
        with self._scope(user_id):
            return await self._notifications(user_id, limit, unread_only)

    async def _notifications(
        self, user_id: str, limit: int, unread_only: bool
    ) -> list[NotificationSummary]:
        async with self._session_factory() as session:
            rows = await SqlAlchemySchedulerStore(session).list_notifications(
                user_id=user_id, limit=limit, unread_only=unread_only
            )
        return [_notification_summary(row) for row in rows]


__all__ = ["SqlAccountDataReader"]
