"""Scheduler & alerts HTTP surface: schedules, alert rules, notifications, alert preferences,
and native-push device registration.

Every route is behind `require_user` and user-scoped — the store filters on `user_id`, so a
caller can never read or mutate another user's schedules. The scheduler *feature* itself is
entitlement-gated (Pro): `_require_scheduler_feature` reuses `accounts.entitlements.feature_allowed`
(the one feature→min-tier table), never a hand-rolled tier comparison. A creation request is also
validated against the scheduling bounds (coarse cadence, real skill) before it is stored.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.entitlements import feature_allowed
from accounts.models import User
from api.dependencies import AppState, get_session, get_state, require_user
from api.errors import ApiError
from scheduler.models import (
    AlertCondition,
    AlertPreference,
    AlertRule,
    DeliveryChannelKind,
    DevicePlatform,
    DevicePushToken,
    Notification,
    NotificationBadge,
    QuietHours,
    ScheduleAction,
    ScheduleJob,
    ScheduleTrigger,
)
from scheduler.store import (
    AlertRuleNotFoundError,
    ScheduleJobNotFoundError,
    SchedulerStore,
    SqlAlchemySchedulerStore,
)
from scheduler.validation import (
    InvalidTriggerError,
    UnknownSkillReferenceError,
    validate_action,
    validate_trigger,
)
from schemas.enums import Market

router = APIRouter(tags=["scheduler"])

_SCHEDULER_FEATURE = "scheduler"
# Numeric literal, matching api/errors.py — the `status.HTTP_422_*` constant name churns across
# Starlette versions and emits a deprecation warning; the code is stable.
_HTTP_422_UNPROCESSABLE = 422


def _store(session: AsyncSession = Depends(get_session)) -> SchedulerStore:
    return SqlAlchemySchedulerStore(session)


def _require_scheduler_feature(user: User) -> None:
    if not feature_allowed(user, _SCHEDULER_FEATURE):
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="feature_not_entitled",
            message="Scheduling requires a Pro subscription.",
            retryable=False,
        )


def _not_found(code: str, message: str) -> ApiError:
    return ApiError(
        status_code=status.HTTP_404_NOT_FOUND, code=code, message=message, retryable=False
    )


# ---- schedules -----------------------------------------------------------------------------


class CreateScheduleRequest(BaseModel):
    trigger: ScheduleTrigger
    action: ScheduleAction
    delivery_channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = Field(default=None, ge=1)


class UpdateScheduleRequest(BaseModel):
    enabled: bool | None = None
    delivery_channels: tuple[DeliveryChannelKind, ...] | None = None
    daily_limit: int | None = Field(default=None, ge=1)


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    body: CreateScheduleRequest,
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> ScheduleJob:
    _require_scheduler_feature(user)
    try:
        validate_trigger(body.trigger)
        validate_action(body.action, state.skill_registry)
    except InvalidTriggerError as exc:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="invalid_trigger",
            message=str(exc),
            retryable=False,
        ) from exc
    except UnknownSkillReferenceError as exc:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="unknown_skill",
            message=str(exc),
            retryable=False,
        ) from exc
    return await store.create_job(
        user_id=user.id,
        trigger=body.trigger,
        action=body.action,
        delivery_channels=body.delivery_channels,
        quiet_hours=body.quiet_hours,
        daily_limit=body.daily_limit,
    )


@router.get("/schedules")
async def list_schedules(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> dict[str, list[ScheduleJob]]:
    _require_scheduler_feature(user)
    return {"schedules": await store.list_jobs(user_id=user.id)}


@router.patch("/schedules/{job_id}")
async def update_schedule(
    job_id: str,
    body: UpdateScheduleRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> ScheduleJob:
    _require_scheduler_feature(user)
    try:
        return await store.update_job(
            user_id=user.id,
            job_id=job_id,
            enabled=body.enabled,
            delivery_channels=body.delivery_channels,
            daily_limit=body.daily_limit,
        )
    except ScheduleJobNotFoundError as exc:
        raise _not_found("schedule_not_found", f"Schedule {job_id} not found.") from exc


@router.delete("/schedules/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    job_id: str,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
    _require_scheduler_feature(user)
    try:
        await store.delete_job(user_id=user.id, job_id=job_id)
    except ScheduleJobNotFoundError as exc:
        raise _not_found("schedule_not_found", f"Schedule {job_id} not found.") from exc


# ---- alerts --------------------------------------------------------------------------------


class CreateAlertRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    market: Market
    condition: AlertCondition
    channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = Field(default=None, ge=1)


class UpdateAlertRequest(BaseModel):
    enabled: bool | None = None


@router.post("/alerts", status_code=status.HTTP_201_CREATED)
async def create_alert(
    body: CreateAlertRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> AlertRule:
    _require_scheduler_feature(user)
    return await store.create_alert(
        user_id=user.id,
        symbol=body.symbol,
        market=body.market,
        condition=body.condition,
        channels=body.channels,
        quiet_hours=body.quiet_hours,
        daily_limit=body.daily_limit,
    )


@router.get("/alerts")
async def list_alerts(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> dict[str, list[AlertRule]]:
    _require_scheduler_feature(user)
    return {"alerts": await store.list_alerts(user_id=user.id)}


@router.patch("/alerts/{alert_id}")
async def update_alert(
    alert_id: str,
    body: UpdateAlertRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> AlertRule:
    _require_scheduler_feature(user)
    try:
        return await store.update_alert(user_id=user.id, alert_id=alert_id, enabled=body.enabled)
    except AlertRuleNotFoundError as exc:
        raise _not_found("alert_not_found", f"Alert {alert_id} not found.") from exc


@router.delete("/alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(
    alert_id: str,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
    _require_scheduler_feature(user)
    try:
        await store.delete_alert(user_id=user.id, alert_id=alert_id)
    except AlertRuleNotFoundError as exc:
        raise _not_found("alert_not_found", f"Alert {alert_id} not found.") from exc


# ---- notifications -------------------------------------------------------------------------


@router.get("/notifications")
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> dict[str, list[Notification]]:
    notifications = await store.list_notifications(
        user_id=user.id, limit=limit, unread_only=unread_only
    )
    return {"notifications": notifications}


@router.get("/notifications/badge")
async def notifications_badge(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> NotificationBadge:
    return await store.badge_counts(user_id=user.id)


# ---- alert preferences ---------------------------------------------------------------------


class AlertPreferenceRequest(BaseModel):
    channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)
    enabled_categories: tuple[str, ...] = Field(default_factory=tuple)
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = Field(default=None, ge=1)
    morning_digest: bool = False


@router.put("/me/alert-preferences")
async def put_alert_preferences(
    body: AlertPreferenceRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> AlertPreference:
    return await store.upsert_preferences(
        user_id=user.id,
        channels=body.channels,
        enabled_categories=body.enabled_categories,
        quiet_hours=body.quiet_hours,
        daily_limit=body.daily_limit,
        morning_digest=body.morning_digest,
    )


# ---- devices -------------------------------------------------------------------------------


class RegisterDeviceRequest(BaseModel):
    platform: DevicePlatform
    token: str = Field(min_length=1, max_length=512)


@router.post("/me/devices", status_code=status.HTTP_201_CREATED)
async def register_device(
    body: RegisterDeviceRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> DevicePushToken:
    return await store.register_device(user_id=user.id, platform=body.platform, token=body.token)


@router.delete("/me/devices/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device(
    token: str,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
    await store.unregister_device(user_id=user.id, token=token)


__all__ = ["router"]
