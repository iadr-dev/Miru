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
from datetime import UTC, date, datetime
from typing import Protocol, runtime_checkable

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    AlertPreferenceRow,
    AlertRuleRow,
    DevicePushTokenRow,
    NotificationRow,
    ScheduleJobRow,
)
from scheduler.models import (
    AlertCondition,
    AlertPreference,
    AlertRule,
    DeliveryChannelKind,
    DevicePlatform,
    DevicePushToken,
    JobStatus,
    Notification,
    NotificationBadge,
    NotificationSource,
    QuietHours,
    ScheduleAction,
    ScheduleJob,
    ScheduleTrigger,
    TriggerType,
    dedupe_key_for,
)
from schemas.enums import Market

logger = structlog.get_logger(__name__)


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


def _job_to_model(row: ScheduleJobRow) -> ScheduleJob:
    return ScheduleJob(
        id=row.id,
        user_id=row.user_id,
        trigger=ScheduleTrigger(
            type=TriggerType(row.trigger_type),
            spec=row.trigger_spec,
            market=Market(row.trigger_market),
        ),
        action=ScheduleAction(skill_ref=row.skill_ref, inputs=row.skill_inputs),
        delivery_channels=_channels(row.delivery_channels),
        quiet_hours=_quiet(row.quiet_start, row.quiet_end),
        daily_limit=row.daily_limit,
        enabled=row.enabled,
        last_run=row.last_run,
        next_run=row.next_run,
        status=JobStatus(row.status),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _alert_to_model(row: AlertRuleRow) -> AlertRule:
    return AlertRule(
        id=row.id,
        user_id=row.user_id,
        symbol=row.symbol,
        market=Market(row.market),
        condition=AlertCondition.model_validate(row.condition),
        channels=_channels(row.channels),
        quiet_hours=_quiet(row.quiet_start, row.quiet_end),
        daily_limit=row.daily_limit,
        enabled=row.enabled,
        last_triggered=row.last_triggered,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _pref_to_model(row: AlertPreferenceRow) -> AlertPreference:
    return AlertPreference(
        user_id=row.user_id,
        channels=_channels(row.channels),
        enabled_categories=tuple(row.enabled_categories),
        quiet_hours=_quiet(row.quiet_start, row.quiet_end),
        daily_limit=row.daily_limit,
        morning_digest=row.morning_digest,
        updated_at=row.updated_at,
    )


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

    async def update_job(  # noqa: PLR0913 — one keyword field per editable job dimension
        self,
        *,
        user_id: str,
        job_id: str,
        enabled: bool | None = None,
        delivery_channels: tuple[DeliveryChannelKind, ...] | None = None,
        daily_limit: int | None = None,
        expected_version: int | None = None,
    ) -> ScheduleJob: ...

    async def delete_job(self, *, user_id: str, job_id: str) -> None: ...

    async def set_job_status(
        self,
        *,
        job_id: str,
        status: JobStatus,
        last_run: datetime | None = None,
        next_run: datetime | None = None,
        disable: bool = False,
    ) -> None: ...

    async def due_jobs(self, *, now: datetime) -> list[ScheduleJob]: ...

    async def create_alert(  # noqa: PLR0913 — one keyword field per persisted alert dimension
        self,
        *,
        user_id: str,
        symbol: str,
        market: Market,
        condition: AlertCondition,
        channels: tuple[DeliveryChannelKind, ...],
        quiet_hours: QuietHours | None = None,
        daily_limit: int | None = None,
    ) -> AlertRule: ...

    async def list_alerts(self, *, user_id: str) -> list[AlertRule]: ...

    async def get_alert(self, *, user_id: str, alert_id: str) -> AlertRule: ...

    async def update_alert(
        self, *, user_id: str, alert_id: str, enabled: bool | None = None
    ) -> AlertRule: ...

    async def delete_alert(self, *, user_id: str, alert_id: str) -> None: ...

    async def mark_alert_triggered(self, *, alert_id: str, when: datetime) -> None: ...

    async def enabled_alerts(self) -> list[AlertRule]: ...

    async def get_preferences(self, *, user_id: str) -> AlertPreference | None: ...

    async def upsert_preferences(  # noqa: PLR0913 — one keyword field per preference dimension
        self,
        *,
        user_id: str,
        channels: tuple[DeliveryChannelKind, ...],
        enabled_categories: tuple[str, ...],
        quiet_hours: QuietHours | None,
        daily_limit: int | None,
        morning_digest: bool,
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
    ) -> Notification | None: ...

    async def list_notifications(
        self, *, user_id: str, limit: int = 50, unread_only: bool = False
    ) -> list[Notification]: ...

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

    async def _get_job_row(self, *, user_id: str, job_id: str) -> ScheduleJobRow:
        result = await self._session.execute(
            select(ScheduleJobRow).where(
                ScheduleJobRow.id == job_id, ScheduleJobRow.user_id == user_id
            )
        )
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

    async def update_job(  # noqa: PLR0913 — one keyword field per editable job dimension
        self,
        *,
        user_id: str,
        job_id: str,
        enabled: bool | None = None,
        delivery_channels: tuple[DeliveryChannelKind, ...] | None = None,
        daily_limit: int | None = None,
        expected_version: int | None = None,
    ) -> ScheduleJob:
        row = await self._get_job_row(user_id=user_id, job_id=job_id)
        if expected_version is not None and row.version != expected_version:
            raise StaleVersionError(job_id)
        if enabled is not None:
            row.enabled = enabled
        if delivery_channels is not None:
            row.delivery_channels = [c.value for c in delivery_channels]
        if daily_limit is not None:
            row.daily_limit = daily_limit
        row.version += 1
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return _job_to_model(row)

    async def delete_job(self, *, user_id: str, job_id: str) -> None:
        row = await self._get_job_row(user_id=user_id, job_id=job_id)
        await self._session.delete(row)
        await self._session.flush()
        logger.info("schedule_job_deleted", user_id=user_id, job_id=job_id)

    async def set_job_status(
        self,
        *,
        job_id: str,
        status: JobStatus,
        last_run: datetime | None = None,
        next_run: datetime | None = None,
        disable: bool = False,
    ) -> None:
        result = await self._session.execute(
            select(ScheduleJobRow).where(ScheduleJobRow.id == job_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.status = status.value
        if last_run is not None:
            row.last_run = last_run
        if next_run is not None:
            row.next_run = next_run
        if disable:
            row.enabled = False
        row.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def due_jobs(self, *, now: datetime) -> list[ScheduleJob]:
        """Cross-user scan for the leader worker: enabled jobs whose `next_run` has arrived."""
        result = await self._session.execute(
            select(ScheduleJobRow).where(
                ScheduleJobRow.enabled.is_(True),
                ScheduleJobRow.next_run.is_not(None),
                ScheduleJobRow.next_run <= now,
            )
        )
        return [_job_to_model(row) for row in result.scalars().all()]

    # ---- alerts ----

    async def create_alert(  # noqa: PLR0913 — one keyword field per persisted alert dimension
        self,
        *,
        user_id: str,
        symbol: str,
        market: Market,
        condition: AlertCondition,
        channels: tuple[DeliveryChannelKind, ...],
        quiet_hours: QuietHours | None = None,
        daily_limit: int | None = None,
    ) -> AlertRule:
        now = datetime.now(UTC)
        row = AlertRuleRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            symbol=symbol,
            market=market.value,
            condition=condition.model_dump(mode="json"),
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

    async def _get_alert_row(self, *, user_id: str, alert_id: str) -> AlertRuleRow:
        result = await self._session.execute(
            select(AlertRuleRow).where(AlertRuleRow.id == alert_id, AlertRuleRow.user_id == user_id)
        )
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
        self, *, user_id: str, alert_id: str, enabled: bool | None = None
    ) -> AlertRule:
        row = await self._get_alert_row(user_id=user_id, alert_id=alert_id)
        if enabled is not None:
            row.enabled = enabled
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

    async def enabled_alerts(self) -> list[AlertRule]:
        result = await self._session.execute(
            select(AlertRuleRow).where(AlertRuleRow.enabled.is_(True))
        )
        return [_alert_to_model(row) for row in result.scalars().all()]

    # ---- preferences ----

    async def get_preferences(self, *, user_id: str) -> AlertPreference | None:
        result = await self._session.execute(
            select(AlertPreferenceRow).where(AlertPreferenceRow.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        return _pref_to_model(row) if row is not None else None

    async def upsert_preferences(  # noqa: PLR0913 — one keyword field per preference dimension
        self,
        *,
        user_id: str,
        channels: tuple[DeliveryChannelKind, ...],
        enabled_categories: tuple[str, ...],
        quiet_hours: QuietHours | None,
        daily_limit: int | None,
        morning_digest: bool,
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
        row.daily_limit = daily_limit
        row.morning_digest = morning_digest
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

    async def list_notifications(
        self, *, user_id: str, limit: int = 50, unread_only: bool = False
    ) -> list[Notification]:
        stmt = select(NotificationRow).where(NotificationRow.user_id == user_id)
        if unread_only:
            stmt = stmt.where(NotificationRow.read_at.is_(None))
        stmt = stmt.order_by(NotificationRow.delivered_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return [_notification_to_model(row) for row in result.scalars().all()]

    async def badge_counts(self, *, user_id: str) -> NotificationBadge:
        unread_notifications = await self._session.scalar(
            select(func.count())
            .select_from(NotificationRow)
            .where(NotificationRow.user_id == user_id, NotificationRow.read_at.is_(None))
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
    "AlertRuleNotFoundError",
    "ScheduleJobNotFoundError",
    "SchedulerStore",
    "SqlAlchemySchedulerStore",
    "StaleVersionError",
]
