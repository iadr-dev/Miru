"""`SchedulerStore` — SQLAlchemy CRUD for jobs, alerts, preferences, notifications, and device
tokens.

Same per-request-session convention as `conversation.store` / `accounts.store`: the caller owns
the session/transaction boundary; every method only `flush`es. Every read/write filters on
`user_id` so one user can never touch another's schedules — the per-user isolation invariant.

The worker uses the two unscoped scan methods (`due_jobs`, `enabled_alerts`) that deliberately
span users — those are the only cross-user reads, and they exist solely so the leader worker can
find due work; they never leak one user's data into another's delivery, because each fired
job/alert carries its own `user_id` through to persistence and delivery.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Protocol, runtime_checkable

import structlog
from pydantic import TypeAdapter
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    AlertEvalMarkRow,
    AlertPreferenceRow,
    AlertRuleRow,
    ChannelLinkRow,
    DevicePushTokenRow,
    EmailSuppressionRow,
    NotificationRow,
    OAuthAccountRow,
    ScheduleJobRow,
    WatchlistItemRow,
    WatchlistRow,
)
from scheduler.models import (
    DEFAULT_QUIET_HOURS,
    DEFAULT_TIMEZONE,
    AlertPatch,
    AlertPreference,
    AlertRule,
    BriefSymbols,
    DeliveryChannelKind,
    DevicePlatform,
    DevicePushToken,
    EmailBlockReason,
    JobStatus,
    Notification,
    NotificationBadge,
    NotificationSource,
    PendingDelivery,
    QuietHours,
    ScheduleAction,
    ScheduleJob,
    SchedulePatch,
    ScheduleTrigger,
    TriggerMode,
    TriggerType,
    dedupe_key_for,
)
from scheduler.templates import ConditionModel, RuleCondition
from schemas.enums import Market, user_market

logger = structlog.get_logger(__name__)

# How long a job may stay `running` before the mark is treated as an orphaned run (the worker
# crashed mid-run) and the job becomes due again.
RUN_TIMEOUT = timedelta(minutes=15)
# A notification recorded as pending but never sent (the process died between the commit and the
# send) is picked up by the retry sweep once it is older than this, so the original send is not
# raced.
FIRST_SEND_GRACE = timedelta(minutes=2)

_CONDITION = TypeAdapter[ConditionModel](RuleCondition)


class ScheduleJobNotFoundError(LookupError):
    pass


class AlertRuleNotFoundError(LookupError):
    pass


class StaleVersionError(RuntimeError):
    """A job update lost the optimistic-lock race (worker vs. a concurrent user edit)."""


# ---- row <-> model mappers -----------------------------------------------------------------


def _channels(raw: list[str]) -> tuple[DeliveryChannelKind, ...]:
    return tuple(DeliveryChannelKind(c) for c in raw)


def _quiet(start: str | None, end: str | None) -> QuietHours | None:
    if start is None or end is None:
        return None
    return QuietHours(start=start, end=end)


def _apply_job_target(row: ScheduleJobRow, patch: SchedulePatch, given: set[str]) -> None:
    """The trigger and action parts of a user edit, when they were sent."""
    if "trigger" in given and patch.trigger is not None:
        row.trigger_type = patch.trigger.type.value
        row.trigger_spec = patch.trigger.spec
        row.trigger_market = patch.trigger.market.value
    if "action" in given and patch.action is not None:
        row.skill_ref = patch.action.skill_ref
        row.skill_inputs = dict(patch.action.inputs)


def _apply_alert_rule_fields(row: AlertRuleRow, patch: AlertPatch, given: set[str]) -> None:
    """The rule-defining parts of a user edit. A new condition resets the edge-trigger memory."""
    if "enabled" in given and patch.enabled is not None:
        row.enabled = patch.enabled
    if "condition" in given and patch.condition is not None:
        row.condition = patch.condition.model_dump(mode="json")
        row.state = {}
    if "trigger_mode" in given and patch.trigger_mode is not None:
        row.trigger_mode = patch.trigger_mode.value
    if "cooldown_seconds" in given:
        row.cooldown_seconds = patch.cooldown_seconds
    if "expires_at" in given:
        row.expires_at = patch.expires_at


def _apply_quiet_and_limit(
    row: ScheduleJobRow | AlertRuleRow, patch: SchedulePatch | AlertPatch, given: set[str]
) -> None:
    """Quiet hours and the daily limit are nullable: sending `None` clears them."""
    if "quiet_hours" in given:
        quiet = patch.quiet_hours
        row.quiet_start = quiet.start if quiet else None
        row.quiet_end = quiet.end if quiet else None
    if "daily_limit" in given:
        row.daily_limit = patch.daily_limit


def _job_to_model(row: ScheduleJobRow) -> ScheduleJob:
    return ScheduleJob(
        id=row.id,
        user_id=row.user_id,
        trigger=ScheduleTrigger(
            type=TriggerType(row.trigger_type),
            spec=row.trigger_spec,
            market=user_market(row.trigger_market),
        ),
        action=ScheduleAction(skill_ref=row.skill_ref, inputs=row.skill_inputs),
        delivery_channels=_channels(row.delivery_channels),
        quiet_hours=_quiet(row.quiet_start, row.quiet_end),
        daily_limit=row.daily_limit,
        enabled=row.enabled,
        last_run=row.last_run,
        next_run=row.next_run,
        status=JobStatus(row.status),
        status_reason=row.status_reason,
        thread_id=row.thread_id,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _email_block_reason(value: str) -> EmailBlockReason:
    """A stored suppression reason as the literal; the column only ever holds one of the three."""
    if value == "bounced":
        return "bounced"
    if value == "complained":
        return "complained"
    if value == "unsubscribed":
        return "unsubscribed"
    raise ValueError(f"{value!r} is not an email suppression reason")


def _alert_to_model(row: AlertRuleRow) -> AlertRule:
    return AlertRule(
        id=row.id,
        user_id=row.user_id,
        symbol=row.symbol,
        market=user_market(row.market) if row.market is not None else None,
        watchlist_id=row.watchlist_id,
        origin_watchlist_id=row.origin_watchlist_id,
        condition=_CONDITION.validate_python(row.condition),
        trigger_mode=TriggerMode(row.trigger_mode),
        cooldown_seconds=row.cooldown_seconds,
        state=dict(row.state),
        expires_at=row.expires_at,
        auto_paused_reason=row.auto_paused_reason,
        explain=row.explain,
        channels=_channels(row.channels),
        quiet_hours=_quiet(row.quiet_start, row.quiet_end),
        daily_limit=row.daily_limit,
        enabled=row.enabled,
        last_triggered=row.last_triggered,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _pref_to_model(row: AlertPreferenceRow) -> AlertPreference:
    return AlertPreference(
        user_id=row.user_id,
        channels=_channels(row.channels),
        enabled_categories=tuple(row.enabled_categories),
        # No stored window means "never chosen": the default preset. Turning quiet hours off is an
        # explicit empty window (start == end), which `_within_quiet_hours` never matches.
        quiet_hours=_quiet(row.quiet_start, row.quiet_end) or DEFAULT_QUIET_HOURS,
        morning_digest=row.morning_digest,
        digest_thread_id=row.digest_thread_id,
        timezone=row.timezone,
        updated_at=row.updated_at,
    )


_HELD_SCAN_BATCH = 500
_INBOX_CHANNEL = DeliveryChannelKind.IN_APP.value
_BRIEF_PER_KIND = 5


def _notification_to_model(row: NotificationRow) -> Notification:
    return Notification(
        id=row.id,
        user_id=row.user_id,
        source=NotificationSource(row.source),
        source_id=row.source_id,
        channel=DeliveryChannelKind(row.channel),
        dedupe_key=row.dedupe_key,
        payload=row.payload,
        delivered_at=row.delivered_at,
        read_at=row.read_at,
    )


def _device_to_model(row: DevicePushTokenRow) -> DevicePushToken:
    return DevicePushToken(
        id=row.id,
        user_id=row.user_id,
        platform=DevicePlatform(row.platform),
        token=row.token,
        created_at=row.created_at,
        last_seen=row.last_seen,
    )


@runtime_checkable
class SchedulerStore(Protocol):
    async def create_job(  # noqa: PLR0913 — one keyword field per persisted job dimension
        self,
        *,
        user_id: str,
        trigger: ScheduleTrigger,
        action: ScheduleAction,
        delivery_channels: tuple[DeliveryChannelKind, ...],
        quiet_hours: QuietHours | None = None,
        daily_limit: int | None = None,
        next_run: datetime | None = None,
    ) -> ScheduleJob: ...

    async def list_jobs(self, *, user_id: str) -> list[ScheduleJob]: ...

    async def get_job(self, *, user_id: str, job_id: str) -> ScheduleJob: ...

    async def update_job(
        self,
        *,
        user_id: str,
        job_id: str,
        patch: SchedulePatch,
        next_run: datetime | None = None,
        expected_version: int | None = None,
    ) -> ScheduleJob: ...

    async def delete_job(self, *, user_id: str, job_id: str) -> None: ...

    async def set_job_status(  # noqa: PLR0913 — one keyword field per run-outcome dimension
        self,
        *,
        job_id: str,
        status: JobStatus,
        last_run: datetime | None = None,
        next_run: datetime | None = None,
        disable: bool = False,
        reason: str | None = None,
        thread_id: str | None = None,
    ) -> None: ...

    async def due_jobs(self, *, now: datetime) -> list[ScheduleJob]: ...

    async def claim_job(self, *, job_id: str, now: datetime) -> bool: ...

    async def create_alert(  # noqa: PLR0913 — one keyword field per persisted alert dimension
        self,
        *,
        user_id: str,
        condition: ConditionModel,
        symbol: str | None = None,
        market: Market | None = None,
        watchlist_id: str | None = None,
        origin_watchlist_id: str | None = None,
        trigger_mode: TriggerMode = TriggerMode.ONCE_PER_DAY,
        cooldown_seconds: int | None = None,
        expires_at: datetime | None = None,
        explain: bool = False,
        channels: tuple[DeliveryChannelKind, ...],
        quiet_hours: QuietHours | None = None,
        daily_limit: int | None = None,
    ) -> AlertRule: ...

    async def list_alerts(self, *, user_id: str) -> list[AlertRule]: ...

    async def get_alert(self, *, user_id: str, alert_id: str) -> AlertRule: ...

    async def update_alert(
        self,
        *,
        user_id: str,
        alert_id: str,
        patch: AlertPatch,
        expected_version: int | None = None,
    ) -> AlertRule: ...

    async def delete_alert(self, *, user_id: str, alert_id: str) -> None: ...

    async def mark_alert_triggered(self, *, alert_id: str, when: datetime) -> None: ...

    async def save_alert_state(
        self,
        *,
        alert_id: str,
        state: dict[str, object],
        last_triggered: datetime | None = None,
        enabled: bool | None = None,
        auto_paused_reason: str | None = None,
    ) -> None: ...

    async def watchlist_members(
        self, *, watchlist_id: str, user_id: str
    ) -> list[tuple[str, Market]]: ...

    async def watchlist_owned_by(self, *, user_id: str, watchlist_id: str) -> bool: ...

    async def get_eval_mark(self, *, capability: str, market: str) -> str | None: ...

    async def set_eval_mark(self, *, capability: str, market: str, as_of: str) -> None: ...

    async def enabled_alerts(self) -> list[AlertRule]: ...

    async def get_preferences(self, *, user_id: str) -> AlertPreference | None: ...

    async def morning_digest_user_ids(self) -> list[str]: ...

    async def set_digest_thread(self, *, user_id: str, thread_id: str) -> None: ...

    async def brief_symbols(self, *, user_id: str) -> BriefSymbols: ...

    async def recent_inbox(
        self, *, user_id: str, since: datetime, limit: int = 50
    ) -> list[Notification]: ...

    async def upsert_preferences(  # noqa: PLR0913 — one keyword field per preference dimension
        self,
        *,
        user_id: str,
        channels: tuple[DeliveryChannelKind, ...],
        enabled_categories: tuple[str, ...],
        quiet_hours: QuietHours | None,
        morning_digest: bool,
        timezone: str = "Asia/Taipei",
    ) -> AlertPreference: ...

    async def record_notification(  # noqa: PLR0913 — one keyword field per delivery dimension
        self,
        *,
        user_id: str,
        source: NotificationSource,
        source_id: str,
        channel: DeliveryChannelKind,
        trigger_date: date,
        payload: dict[str, object],
        delivery_state: str = "sent",
    ) -> Notification | None: ...

    async def mark_delivery(
        self,
        *,
        notification_id: str,
        state: str,
        attempts: int,
        next_attempt_at: datetime | None,
    ) -> None: ...

    async def claim_due_deliveries(
        self, *, now: datetime, limit: int, lease: timedelta
    ) -> list[PendingDelivery]: ...

    async def list_notifications(
        self, *, user_id: str, limit: int = 50, unread_only: bool = False
    ) -> list[Notification]: ...

    async def mark_notifications_read(
        self, *, user_id: str, ids: tuple[str, ...] | None = None
    ) -> int: ...

    async def list_held_notifications(
        self, *, since: datetime, limit: int = 500
    ) -> list[Notification]: ...

    async def release_held_notifications(self, *, ids: tuple[str, ...]) -> int: ...

    async def start_channel_link(
        self, *, user_id: str, channel: str, token: str, expires_at: datetime
    ) -> None: ...

    async def complete_channel_link(
        self, *, channel: str, token: str, address: str, now: datetime
    ) -> str | None: ...

    async def channel_address(self, *, user_id: str, channel: str) -> str | None: ...

    async def unlink_channel(self, *, user_id: str, channel: str) -> None: ...

    async def unlink_line_user(self, *, line_user_id: str) -> None: ...

    async def suppress_email(
        self, *, address: str, reason: str, event_id: str | None = None
    ) -> None: ...

    async def is_email_suppressed(self, *, address: str) -> bool: ...

    async def clear_unsubscribe(self, *, address: str) -> None: ...

    async def email_suppression_reason(self, *, address: str) -> EmailBlockReason | None: ...

    async def linked_line_user_id(self, *, user_id: str) -> str | None: ...

    async def count_jobs(self, *, user_id: str) -> int: ...

    async def count_alerts(self, *, user_id: str) -> int: ...

    async def badge_counts(self, *, user_id: str) -> NotificationBadge: ...

    async def deliveries_today(
        self, *, user_id: str, source_id: str, trigger_date: date
    ) -> int: ...

    async def register_device(
        self, *, user_id: str, platform: DevicePlatform, token: str
    ) -> DevicePushToken: ...

    async def unregister_device(self, *, user_id: str, token: str) -> None: ...

    async def list_devices(self, *, user_id: str) -> list[DevicePushToken]: ...


class SqlAlchemySchedulerStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    # ---- jobs ----

    async def create_job(  # noqa: PLR0913 — one keyword field per persisted job dimension
        self,
        *,
        user_id: str,
        trigger: ScheduleTrigger,
        action: ScheduleAction,
        delivery_channels: tuple[DeliveryChannelKind, ...],
        quiet_hours: QuietHours | None = None,
        daily_limit: int | None = None,
        next_run: datetime | None = None,
    ) -> ScheduleJob:
        now = datetime.now(UTC)
        row = ScheduleJobRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            trigger_type=trigger.type.value,
            trigger_spec=trigger.spec,
            trigger_market=trigger.market.value,
            skill_ref=action.skill_ref,
            skill_inputs=dict(action.inputs),
            read_only=True,
            delivery_channels=[c.value for c in delivery_channels],
            quiet_start=quiet_hours.start if quiet_hours else None,
            quiet_end=quiet_hours.end if quiet_hours else None,
            daily_limit=daily_limit,
            enabled=True,
            next_run=next_run,
            status=JobStatus.OK.value,
            version=0,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("schedule_job_created", user_id=user_id, job_id=row.id)
        return _job_to_model(row)

    async def _get_job_row(
        self, *, user_id: str, job_id: str, lock: bool = False
    ) -> ScheduleJobRow:
        """The user's schedule row.

        `lock=True` takes a row lock (`SELECT … FOR UPDATE`, a no-op on SQLite) for the edit
        paths, so a second concurrent edit waits and then sees the fresh `version`; without
        it two editors could both pass the version check and the last write would silently
        win."""
        statement = select(ScheduleJobRow).where(
            ScheduleJobRow.id == job_id, ScheduleJobRow.user_id == user_id
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        if row is None:
            raise ScheduleJobNotFoundError(job_id)
        return row

    async def list_jobs(self, *, user_id: str) -> list[ScheduleJob]:
        result = await self._session.execute(
            select(ScheduleJobRow)
            .where(ScheduleJobRow.user_id == user_id)
            .order_by(ScheduleJobRow.created_at.desc())
        )
        return [_job_to_model(row) for row in result.scalars().all()]

    async def get_job(self, *, user_id: str, job_id: str) -> ScheduleJob:
        return _job_to_model(await self._get_job_row(user_id=user_id, job_id=job_id))

    async def update_job(
        self,
        *,
        user_id: str,
        job_id: str,
        patch: SchedulePatch,
        next_run: datetime | None = None,
        expected_version: int | None = None,
    ) -> ScheduleJob:
        """Apply a user edit under optimistic locking: a stale `expected_version` raises
        `StaleVersionError` (-> 409). Every edit bumps `version`. A new trigger or re-enabling takes
        the caller-resolved `next_run` (the router owns trigger resolution) and clears the last
        run's status reason."""
        row = await self._get_job_row(user_id=user_id, job_id=job_id, lock=True)
        if expected_version is not None and row.version != expected_version:
            raise StaleVersionError(job_id)
        given = patch.model_fields_set
        if "enabled" in given and patch.enabled is not None:
            row.enabled = patch.enabled
        _apply_job_target(row, patch, given)
        if "delivery_channels" in given and patch.delivery_channels is not None:
            row.delivery_channels = [c.value for c in patch.delivery_channels]
        _apply_quiet_and_limit(row, patch, given)
        if next_run is not None:
            row.next_run = next_run
        if given & {"enabled", "trigger"}:
            row.status_reason = None
        row.version += 1
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return _job_to_model(row)

    async def delete_job(self, *, user_id: str, job_id: str) -> None:
        row = await self._get_job_row(user_id=user_id, job_id=job_id)
        await self._session.delete(row)
        await self._session.flush()
        logger.info("schedule_job_deleted", user_id=user_id, job_id=job_id)

    async def set_job_status(  # noqa: PLR0913 — one keyword field per run-outcome dimension
        self,
        *,
        job_id: str,
        status: JobStatus,
        last_run: datetime | None = None,
        next_run: datetime | None = None,
        disable: bool = False,
        reason: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        result = await self._session.execute(
            select(ScheduleJobRow).where(ScheduleJobRow.id == job_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.status = status.value
        row.status_reason = reason
        if thread_id is not None:
            row.thread_id = thread_id
        if last_run is not None:
            row.last_run = last_run
        if next_run is not None:
            row.next_run = next_run
        if disable:
            row.enabled = False
        row.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def due_jobs(self, *, now: datetime) -> list[ScheduleJob]:
        """Cross-user scan for the leader worker: enabled jobs whose `next_run` has arrived.

        A job marked `running` is skipped while that mark is fresh (it is mid-run, and a second
        poll must not start a duplicate) but becomes due again once the mark is older than
        `RUN_TIMEOUT` — which is how a run orphaned by a crash is recovered."""
        stale_before = now - RUN_TIMEOUT
        result = await self._session.execute(
            select(ScheduleJobRow).where(
                ScheduleJobRow.enabled.is_(True),
                ScheduleJobRow.next_run.is_not(None),
                ScheduleJobRow.next_run <= now,
                or_(
                    ScheduleJobRow.status != JobStatus.RUNNING.value,
                    ScheduleJobRow.updated_at < stale_before,
                ),
            )
        )
        return [_job_to_model(row) for row in result.scalars().all()]

    async def claim_job(self, *, job_id: str, now: datetime) -> bool:
        """Take a due job for this worker in one atomic statement, marking it running.

        `due_jobs` is only a scan: two workers can both see the same job. This is what decides who
        runs it. The row is taken only while it is still due, enabled, and either not running or
        running with a mark older than `RUN_TIMEOUT` (an orphan), so a worker that arrives after
        another has taken the job, or has finished it and moved `next_run` forward, gets False and
        must stand down. Returns True when this call took it."""
        stale_before = now - RUN_TIMEOUT
        result = await self._session.execute(
            update(ScheduleJobRow)
            .where(
                ScheduleJobRow.id == job_id,
                ScheduleJobRow.enabled.is_(True),
                ScheduleJobRow.next_run.is_not(None),
                ScheduleJobRow.next_run <= now,
                or_(
                    ScheduleJobRow.status != JobStatus.RUNNING.value,
                    ScheduleJobRow.updated_at < stale_before,
                ),
            )
            .values(status=JobStatus.RUNNING.value, updated_at=datetime.now(UTC))
        )
        return isinstance(result, CursorResult) and result.rowcount == 1

    # ---- alerts ----

    async def create_alert(  # noqa: PLR0913 — one keyword field per persisted alert dimension
        self,
        *,
        user_id: str,
        condition: ConditionModel,
        symbol: str | None = None,
        market: Market | None = None,
        watchlist_id: str | None = None,
        origin_watchlist_id: str | None = None,
        trigger_mode: TriggerMode = TriggerMode.ONCE_PER_DAY,
        cooldown_seconds: int | None = None,
        expires_at: datetime | None = None,
        explain: bool = False,
        channels: tuple[DeliveryChannelKind, ...],
        quiet_hours: QuietHours | None = None,
        daily_limit: int | None = None,
    ) -> AlertRule:
        now = datetime.now(UTC)
        row = AlertRuleRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            symbol=symbol,
            market=market.value if market is not None else None,
            watchlist_id=watchlist_id,
            origin_watchlist_id=origin_watchlist_id,
            condition=condition.model_dump(mode="json"),
            trigger_mode=trigger_mode.value,
            cooldown_seconds=cooldown_seconds,
            state={},
            expires_at=expires_at,
            explain=explain,
            version=0,
            channels=[c.value for c in channels],
            quiet_start=quiet_hours.start if quiet_hours else None,
            quiet_end=quiet_hours.end if quiet_hours else None,
            daily_limit=daily_limit,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("alert_rule_created", user_id=user_id, alert_id=row.id)
        return _alert_to_model(row)

    async def _get_alert_row(
        self, *, user_id: str, alert_id: str, lock: bool = False
    ) -> AlertRuleRow:
        """The user's alert rule row.

        `lock=True` takes a row lock (`SELECT … FOR UPDATE`, a no-op on SQLite) for the edit
        paths, so a second concurrent edit waits and then sees the fresh `version`; without
        it two editors could both pass the version check and the last write would silently
        win."""
        statement = select(AlertRuleRow).where(
            AlertRuleRow.id == alert_id, AlertRuleRow.user_id == user_id
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        if row is None:
            raise AlertRuleNotFoundError(alert_id)
        return row

    async def list_alerts(self, *, user_id: str) -> list[AlertRule]:
        result = await self._session.execute(
            select(AlertRuleRow)
            .where(AlertRuleRow.user_id == user_id)
            .order_by(AlertRuleRow.created_at.desc())
        )
        return [_alert_to_model(row) for row in result.scalars().all()]

    async def get_alert(self, *, user_id: str, alert_id: str) -> AlertRule:
        return _alert_to_model(await self._get_alert_row(user_id=user_id, alert_id=alert_id))

    async def update_alert(
        self,
        *,
        user_id: str,
        alert_id: str,
        patch: AlertPatch,
        expected_version: int | None = None,
    ) -> AlertRule:
        """Apply a user edit under optimistic locking: a stale `expected_version` raises
        `StaleVersionError` (→ 409) so a second device cannot silently overwrite the first. Every
        edit bumps `version`. Changing the condition resets the edge-trigger memory (`state`) —
        "is it true now vs. last time" is meaningless across a different condition — and any
        edit that re-enables, re-conditions or re-dates a rule clears a prior auto-pause reason.
        Engine-side writes go through `save_alert_state`, which does not bump `version`."""
        row = await self._get_alert_row(user_id=user_id, alert_id=alert_id, lock=True)
        if expected_version is not None and row.version != expected_version:
            raise StaleVersionError(alert_id)
        given = patch.model_fields_set
        _apply_alert_rule_fields(row, patch, given)
        if "explain" in given and patch.explain is not None:
            row.explain = patch.explain
        if "channels" in given and patch.channels is not None:
            row.channels = [c.value for c in patch.channels]
        _apply_quiet_and_limit(row, patch, given)
        if given & {"enabled", "condition", "expires_at"}:
            row.auto_paused_reason = None
        row.version += 1
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return _alert_to_model(row)

    async def delete_alert(self, *, user_id: str, alert_id: str) -> None:
        row = await self._get_alert_row(user_id=user_id, alert_id=alert_id)
        await self._session.delete(row)
        await self._session.flush()
        logger.info("alert_rule_deleted", user_id=user_id, alert_id=alert_id)

    async def mark_alert_triggered(self, *, alert_id: str, when: datetime) -> None:
        result = await self._session.execute(
            select(AlertRuleRow).where(AlertRuleRow.id == alert_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.last_triggered = when
        row.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def save_alert_state(
        self,
        *,
        alert_id: str,
        state: dict[str, object],
        last_triggered: datetime | None = None,
        enabled: bool | None = None,
        auto_paused_reason: str | None = None,
    ) -> None:
        """Engine-side write of edge-trigger memory and lifecycle. Deliberately does NOT bump
        `version`: that counter is the user-edit CAS, and the engine updating `state` must not
        make a concurrent user edit fail with 409."""
        result = await self._session.execute(
            select(AlertRuleRow).where(AlertRuleRow.id == alert_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.state = dict(state)
        if last_triggered is not None:
            row.last_triggered = last_triggered
        if enabled is not None:
            row.enabled = enabled
        if auto_paused_reason is not None:
            row.auto_paused_reason = auto_paused_reason
        row.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def watchlist_members(
        self, *, watchlist_id: str, user_id: str
    ) -> list[tuple[str, Market]]:
        """The members of the owner's live (not soft-deleted) watchlist. Scoped by `user_id` so a
        rule can never read a list it does not own, even if ownership changed after it was saved."""
        result = await self._session.execute(
            select(WatchlistItemRow.symbol, WatchlistItemRow.market)
            .join(WatchlistRow, WatchlistRow.id == WatchlistItemRow.watchlist_id)
            .where(
                WatchlistItemRow.watchlist_id == watchlist_id,
                WatchlistRow.user_id == user_id,
                WatchlistRow.deleted_at.is_(None),
            )
        )
        return [(symbol, Market(market)) for symbol, market in result.all()]

    async def watchlist_owned_by(self, *, user_id: str, watchlist_id: str) -> bool:
        found = await self._session.scalar(
            select(func.count())
            .select_from(WatchlistRow)
            .where(
                WatchlistRow.id == watchlist_id,
                WatchlistRow.user_id == user_id,
                WatchlistRow.deleted_at.is_(None),
            )
        )
        return bool(found)

    async def get_eval_mark(self, *, capability: str, market: str) -> str | None:
        row = await self._session.get(AlertEvalMarkRow, (capability, market))
        return row.last_as_of if row is not None else None

    async def set_eval_mark(self, *, capability: str, market: str, as_of: str) -> None:
        row = await self._session.get(AlertEvalMarkRow, (capability, market))
        now = datetime.now(UTC)
        if row is None:
            self._session.add(
                AlertEvalMarkRow(
                    capability=capability, market=market, last_as_of=as_of, evaluated_at=now
                )
            )
        else:
            row.last_as_of = as_of
            row.evaluated_at = now
        await self._session.flush()

    async def enabled_alerts(self) -> list[AlertRule]:
        result = await self._session.execute(
            select(AlertRuleRow).where(AlertRuleRow.enabled.is_(True))
        )
        return [_alert_to_model(row) for row in result.scalars().all()]

    # ---- preferences ----

    async def set_digest_thread(self, *, user_id: str, thread_id: str) -> None:
        row = await self._session.get(AlertPreferenceRow, user_id)
        if row is not None:
            row.digest_thread_id = thread_id
            await self._session.flush()

    async def brief_symbols(self, *, user_id: str) -> BriefSymbols:
        """The user's own stocks and ETFs across their live watchlist groups, oldest first and
        de-duplicated, capped per kind — what the morning brief reports on besides the market."""
        result = await self._session.execute(
            select(WatchlistItemRow.symbol, WatchlistItemRow.market, WatchlistItemRow.asset_class)
            .join(WatchlistRow, WatchlistRow.id == WatchlistItemRow.watchlist_id)
            .where(WatchlistRow.user_id == user_id, WatchlistRow.deleted_at.is_(None))
            .order_by(WatchlistItemRow.added_at)
        )
        tw: list[str] = []
        us: list[str] = []
        etfs: list[str] = []
        for symbol, market, asset_class in result.all():
            if asset_class == "etf":
                bucket = etfs
            elif market == Market.TW.value:
                bucket = tw
            elif market == Market.US.value:
                bucket = us
            else:
                continue
            if symbol not in bucket and len(bucket) < _BRIEF_PER_KIND:
                bucket.append(symbol)
        return BriefSymbols(tw_stocks=tuple(tw), us_stocks=tuple(us), etfs=tuple(etfs))

    async def morning_digest_user_ids(self) -> list[str]:
        """Users who switched the morning digest on. Cross-user by design: only the leader-gated
        worker calls it, never a router."""
        result = await self._session.execute(
            select(AlertPreferenceRow.user_id).where(AlertPreferenceRow.morning_digest.is_(True))
        )
        return list(result.scalars().all())

    async def recent_inbox(
        self, *, user_id: str, since: datetime, limit: int = 50
    ) -> list[Notification]:
        """The user's inbox entries since `since`, oldest first, excluding earlier digests."""
        result = await self._session.execute(
            select(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.channel == _INBOX_CHANNEL,
                NotificationRow.source != NotificationSource.DIGEST.value,
                NotificationRow.delivered_at >= since,
            )
            .order_by(NotificationRow.delivered_at)
            .limit(limit)
        )
        return [_notification_to_model(r) for r in result.scalars().all()]

    async def get_preferences(self, *, user_id: str) -> AlertPreference | None:
        result = await self._session.execute(
            select(AlertPreferenceRow).where(AlertPreferenceRow.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return _pref_to_model(row)
        # Never saved: the default preset (quiet 00:00-08:00 Taipei), so it is really in effect —
        # not only shown — until the user changes it. Saving with no window clears it.
        return AlertPreference(
            user_id=user_id,
            quiet_hours=DEFAULT_QUIET_HOURS,
            timezone=DEFAULT_TIMEZONE,
            updated_at=datetime.now(UTC),
        )

    async def upsert_preferences(  # noqa: PLR0913 — one keyword field per preference dimension
        self,
        *,
        user_id: str,
        channels: tuple[DeliveryChannelKind, ...],
        enabled_categories: tuple[str, ...],
        quiet_hours: QuietHours | None,
        morning_digest: bool,
        timezone: str = "Asia/Taipei",
    ) -> AlertPreference:
        result = await self._session.execute(
            select(AlertPreferenceRow).where(AlertPreferenceRow.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        now = datetime.now(UTC)
        if row is None:
            row = AlertPreferenceRow(user_id=user_id, updated_at=now)
            self._session.add(row)
        row.channels = [c.value for c in channels]
        row.enabled_categories = list(enabled_categories)
        row.quiet_start = quiet_hours.start if quiet_hours else None
        row.quiet_end = quiet_hours.end if quiet_hours else None
        row.morning_digest = morning_digest
        row.timezone = timezone
        row.updated_at = now
        await self._session.flush()
        return _pref_to_model(row)

    # ---- notifications ----

    async def record_notification(  # noqa: PLR0913 — one keyword field per delivery dimension
        self,
        *,
        user_id: str,
        source: NotificationSource,
        source_id: str,
        channel: DeliveryChannelKind,
        trigger_date: date,
        payload: dict[str, object],
        delivery_state: str = "sent",
    ) -> Notification | None:
        """Insert a delivered-notification row. Returns `None` if a row with the same
        `(user_id, dedupe_key)` already exists — that is the idempotent-delivery no-op, not an
        error."""
        dedupe_key = dedupe_key_for(
            source_id=source_id, trigger_date=trigger_date.isoformat(), channel=channel
        )
        existing = await self._session.execute(
            select(NotificationRow).where(
                NotificationRow.user_id == user_id, NotificationRow.dedupe_key == dedupe_key
            )
        )
        if existing.scalar_one_or_none() is not None:
            logger.info("notification_dedupe_noop", user_id=user_id, dedupe_key=dedupe_key)
            return None
        row = NotificationRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            source=source.value,
            source_id=source_id,
            channel=channel.value,
            dedupe_key=dedupe_key,
            payload=payload,
            delivered_at=datetime.now(UTC),
            delivery_state=delivery_state,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            # A concurrent worker won the race between the SELECT and the INSERT — the unique
            # constraint is the real guard; treat the collision as the same no-op.
            await self._session.rollback()
            logger.info("notification_dedupe_race_noop", user_id=user_id, dedupe_key=dedupe_key)
            return None
        return _notification_to_model(row)

    async def mark_delivery(
        self,
        *,
        notification_id: str,
        state: str,
        attempts: int,
        next_attempt_at: datetime | None,
    ) -> None:
        row = await self._session.get(NotificationRow, notification_id)
        if row is None:
            return
        row.delivery_state = state
        row.delivery_attempts = attempts
        row.next_attempt_at = next_attempt_at
        await self._session.flush()

    async def claim_due_deliveries(
        self, *, now: datetime, limit: int, lease: timedelta
    ) -> list[PendingDelivery]:
        """External notifications that are due another delivery attempt: a transient failure whose
        retry time has come, or a row recorded but never sent (the process died between the commit
        and the send), once it is older than `FIRST_SEND_GRACE` so the original send is not raced.

        Each row is claimed by pushing its `next_attempt_at` out by `lease`, so a second sweep (or
        a second worker, if the lease were ever lost) skips it while this one is sending; the
        caller commits the claim BEFORE sending, so no row lock is held across a network call."""
        stmt = (
            select(NotificationRow)
            .where(
                NotificationRow.delivery_state == "pending",
                or_(
                    NotificationRow.next_attempt_at <= now,
                    and_(
                        NotificationRow.next_attempt_at.is_(None),
                        NotificationRow.delivered_at <= now - FIRST_SEND_GRACE,
                    ),
                ),
            )
            .order_by(NotificationRow.delivered_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        for row in rows:
            row.next_attempt_at = now + lease
        await self._session.flush()
        return [
            PendingDelivery(
                id=row.id,
                user_id=row.user_id,
                channel=DeliveryChannelKind(row.channel),
                payload=dict(row.payload),
                attempts=row.delivery_attempts,
            )
            for row in rows
        ]

    async def list_notifications(
        self, *, user_id: str, limit: int = 50, unread_only: bool = False
    ) -> list[Notification]:
        stmt = select(NotificationRow).where(
            NotificationRow.user_id == user_id, NotificationRow.channel == _INBOX_CHANNEL
        )
        if unread_only:
            stmt = stmt.where(NotificationRow.read_at.is_(None))
        stmt = stmt.order_by(NotificationRow.delivered_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return [_notification_to_model(row) for row in result.scalars().all()]

    async def mark_notifications_read(
        self, *, user_id: str, ids: tuple[str, ...] | None = None
    ) -> int:
        """Stamp `read_at` on the user's unread notifications — the given `ids`, or all when
        `ids` is None. Already-read rows are left alone, so the call is idempotent. Returns the
        number of rows newly marked read."""
        stmt = select(NotificationRow).where(
            NotificationRow.user_id == user_id,
            NotificationRow.channel == _INBOX_CHANNEL,
            NotificationRow.read_at.is_(None),
        )
        if ids is not None:
            stmt = stmt.where(NotificationRow.id.in_(ids))
        rows = (await self._session.execute(stmt)).scalars().all()
        now = datetime.now(UTC)
        for row in rows:
            row.read_at = now
        await self._session.flush()
        return len(rows)

    async def list_held_notifications(
        self, *, since: datetime, limit: int = 500
    ) -> list[Notification]:
        """External-channel notifications parked during a user's quiet hours (`payload.held`).
        The JSON flag is filtered in Python (portable across SQLite/Postgres), so the window is
        read in keyset-paged batches until `limit` held rows are found or the window is exhausted;
        a single `LIMIT` before the filter would starve held rows behind many already-sent ones."""
        held: list[Notification] = []
        cursor: tuple[datetime, str] | None = None
        while len(held) < limit:
            stmt = (
                select(NotificationRow)
                .where(
                    NotificationRow.channel != _INBOX_CHANNEL,
                    NotificationRow.delivered_at >= since,
                )
                .order_by(NotificationRow.delivered_at, NotificationRow.id)
                .limit(_HELD_SCAN_BATCH)
            )
            if cursor is not None:
                stmt = stmt.where(
                    or_(
                        NotificationRow.delivered_at > cursor[0],
                        and_(
                            NotificationRow.delivered_at == cursor[0],
                            NotificationRow.id > cursor[1],
                        ),
                    )
                )
            rows = (await self._session.execute(stmt)).scalars().all()
            held.extend(_notification_to_model(r) for r in rows if r.payload.get("held") is True)
            if len(rows) < _HELD_SCAN_BATCH:
                break
            cursor = (rows[-1].delivered_at, rows[-1].id)
        return held[:limit]

    async def release_held_notifications(self, *, ids: tuple[str, ...]) -> int:
        if not ids:
            return 0
        rows = (
            (
                await self._session.execute(
                    select(NotificationRow).where(NotificationRow.id.in_(ids))
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.payload = {**row.payload, "held": False}
        await self._session.flush()
        return len(rows)

    async def start_channel_link(
        self, *, user_id: str, channel: str, token: str, expires_at: datetime
    ) -> None:
        """(Re)issue the one-time token a user sends to the bot. A fresh request replaces any
        unused token, and never disturbs an address that is already linked."""
        row = await self._session.get(ChannelLinkRow, (user_id, channel))
        if row is None:
            row = ChannelLinkRow(user_id=user_id, channel=channel)
            self._session.add(row)
        row.link_token = token
        row.token_expires_at = expires_at
        await self._session.flush()

    async def complete_channel_link(
        self, *, channel: str, token: str, address: str, now: datetime
    ) -> str | None:
        """Swap a valid, unexpired token for the chat address. Returns the owning user id, or
        `None` if the token is unknown or expired. The token is single-use."""
        # One conditional UPDATE ... RETURNING: of two concurrent deliveries of the same /start
        # (Telegram retries), only one sees the token still set, so the token is truly single use.
        try:
            result = await self._session.execute(
                update(ChannelLinkRow)
                .where(
                    ChannelLinkRow.channel == channel,
                    ChannelLinkRow.link_token == token,
                    ChannelLinkRow.token_expires_at >= now,
                )
                .values(address=address, link_token=None, token_expires_at=None, linked_at=now)
                .returning(ChannelLinkRow.user_id)
            )
        except IntegrityError:
            # That chat is already bound to another account — never share one chat between users.
            await self._session.rollback()
            return None
        return result.scalar_one_or_none()

    async def channel_address(self, *, user_id: str, channel: str) -> str | None:
        row = await self._session.get(ChannelLinkRow, (user_id, channel))
        return row.address if row is not None else None

    async def unlink_channel(self, *, user_id: str, channel: str) -> None:
        row = await self._session.get(ChannelLinkRow, (user_id, channel))
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()

    async def suppress_email(
        self, *, address: str, reason: str, event_id: str | None = None
    ) -> None:
        """Remember that `address` must not be emailed again. Idempotent: webhook redeliveries and a
        second event for the same address leave the first record as it is."""
        key = address.strip().lower()
        if await self._session.get(EmailSuppressionRow, key) is None:
            self._session.add(
                EmailSuppressionRow(
                    address=key, reason=reason, event_id=event_id, created_at=datetime.now(UTC)
                )
            )
            await self._session.flush()

    async def email_suppression_reason(self, *, address: str) -> EmailBlockReason | None:
        """Why this address is not emailed ('bounced' | 'complained' | 'unsubscribed'), or None."""
        row = await self._session.get(EmailSuppressionRow, address.strip().lower())
        return _email_block_reason(row.reason) if row is not None else None

    async def clear_unsubscribe(self, *, address: str) -> None:
        """Forget an *unsubscribe* (the user chose to get email again). A bounce or a spam complaint
        is never cleared this way — those addresses really cannot be reached."""
        row = await self._session.get(EmailSuppressionRow, address.strip().lower())
        if row is not None and row.reason == "unsubscribed":
            await self._session.delete(row)
            await self._session.flush()

    async def is_email_suppressed(self, *, address: str) -> bool:
        return await self._session.get(EmailSuppressionRow, address.strip().lower()) is not None

    async def unlink_line_user(self, *, line_user_id: str) -> None:
        """Drop the link for a LINE user who blocked or removed the official account."""
        result = await self._session.execute(
            select(ChannelLinkRow).where(
                ChannelLinkRow.channel == "line", ChannelLinkRow.address == line_user_id
            )
        )
        for row in result.scalars().all():
            await self._session.delete(row)
        await self._session.flush()

    async def linked_line_user_id(self, *, user_id: str) -> str | None:
        """Where to push a LINE message: the id from the explicit link (the user sent a code to the
        official account, so it is the Messaging user id), else the LINE sign-in id. The sign-in id
        matches only when Login and Messaging channels share a provider, and the push only lands if
        the user has added the official account — hence the explicit link."""
        linked = await self.channel_address(user_id=user_id, channel="line")
        if linked is not None:
            return linked
        sign_in_id: str | None = await self._session.scalar(
            select(OAuthAccountRow.provider_account_id).where(
                OAuthAccountRow.user_id == user_id, OAuthAccountRow.provider == "line"
            )
        )
        return sign_in_id

    async def count_jobs(self, *, user_id: str) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(ScheduleJobRow)
            .where(ScheduleJobRow.user_id == user_id)
        )
        return int(count or 0)

    async def count_alerts(self, *, user_id: str) -> int:
        count = await self._session.scalar(
            select(func.count()).select_from(AlertRuleRow).where(AlertRuleRow.user_id == user_id)
        )
        return int(count or 0)

    async def badge_counts(self, *, user_id: str) -> NotificationBadge:
        unread_notifications = await self._session.scalar(
            select(func.count())
            .select_from(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.channel == _INBOX_CHANNEL,
                NotificationRow.read_at.is_(None),
            )
        )
        pending_alerts = await self._session.scalar(
            select(func.count())
            .select_from(AlertRuleRow)
            .where(AlertRuleRow.user_id == user_id, AlertRuleRow.enabled.is_(True))
        )
        return NotificationBadge(
            unread_notifications=int(unread_notifications or 0),
            unread_threads=0,
            pending_alerts=int(pending_alerts or 0),
        )

    async def deliveries_today(self, *, user_id: str, source_id: str, trigger_date: date) -> int:
        prefix = f"{source_id}:{trigger_date.isoformat()}:"
        count = await self._session.scalar(
            select(func.count())
            .select_from(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.dedupe_key.like(f"{prefix}%"),
            )
        )
        return int(count or 0)

    # ---- devices ----

    async def register_device(
        self, *, user_id: str, platform: DevicePlatform, token: str
    ) -> DevicePushToken:
        """Idempotent on `(user_id, token)`: an existing token refreshes `last_seen` (and its
        platform) rather than inserting a duplicate."""
        result = await self._session.execute(
            select(DevicePushTokenRow).where(
                DevicePushTokenRow.user_id == user_id, DevicePushTokenRow.token == token
            )
        )
        row = result.scalar_one_or_none()
        now = datetime.now(UTC)
        if row is not None:
            row.platform = platform.value
            row.last_seen = now
            await self._session.flush()
            return _device_to_model(row)
        row = DevicePushTokenRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            platform=platform.value,
            token=token,
            created_at=now,
            last_seen=now,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("device_registered", user_id=user_id, platform=platform.value)
        return _device_to_model(row)

    async def unregister_device(self, *, user_id: str, token: str) -> None:
        result = await self._session.execute(
            select(DevicePushTokenRow).where(
                DevicePushTokenRow.user_id == user_id, DevicePushTokenRow.token == token
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()
            logger.info("device_unregistered", user_id=user_id)

    async def list_devices(self, *, user_id: str) -> list[DevicePushToken]:
        result = await self._session.execute(
            select(DevicePushTokenRow).where(DevicePushTokenRow.user_id == user_id)
        )
        return [_device_to_model(row) for row in result.scalars().all()]


__all__ = [
    "RUN_TIMEOUT",
    "AlertRuleNotFoundError",
    "ScheduleJobNotFoundError",
    "SchedulerStore",
    "SqlAlchemySchedulerStore",
    "StaleVersionError",
]
