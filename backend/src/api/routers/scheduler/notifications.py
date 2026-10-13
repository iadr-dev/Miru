"""The notification inbox, read state, badge and alert preferences."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator

from accounts.models import User
from api.dependencies import (
    require_user,
)
from api.errors import ApiError
from api.routers.scheduler._common import HTTP_422_UNPROCESSABLE as _HTTP_422_UNPROCESSABLE
from api.routers.scheduler._common import store as _store
from scheduler.models import (
    AlertPreference,
    DeliveryChannelKind,
    Notification,
    NotificationBadge,
    QuietHours,
)
from scheduler.store import (
    SchedulerStore,
)

router = APIRouter()


class NotificationsResponse(BaseModel):
    notifications: list[Notification]


@router.get("/notifications")
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> NotificationsResponse:
    notifications = await store.list_notifications(
        user_id=user.id, limit=limit, unread_only=unread_only
    )
    return NotificationsResponse(notifications=notifications)


class MarkReadRequest(BaseModel):
    ids: tuple[str, ...] | None = Field(default=None, max_length=200)
    all: bool = False


@router.post("/notifications/read")
async def mark_notifications_read(
    body: MarkReadRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> dict[str, int]:
    """Mark notifications read — `{ids: [...]}` for specific ones, `{all: true}` for everything.
    Exactly one of the two must be given; idempotent, returns how many rows changed."""
    if body.all == (body.ids is not None):
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="invalid_request",
            message="Provide exactly one of `ids` or `all: true`.",
            retryable=False,
        )
    marked = await store.mark_notifications_read(
        user_id=user.id, ids=None if body.all else body.ids
    )
    return {"marked": marked}


@router.get("/notifications/badge")
async def notifications_badge(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> NotificationBadge:
    return await store.badge_counts(user_id=user.id)


class AlertPreferenceRequest(BaseModel):
    channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)
    enabled_categories: tuple[str, ...] = Field(default_factory=tuple)
    quiet_hours: QuietHours | None = None
    morning_digest: bool = False
    # IANA zone the quiet-hours window is evaluated in.
    timezone: str = Field(default="Asia/Taipei", max_length=64)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value


@router.get("/me/alert-preferences")
async def get_alert_preferences(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> AlertPreference:
    """The user's current defaults; a user who never saved any gets the empty defaults (in-app
    only, no quiet hours) rather than a 404, so the settings form always has something to show."""
    existing = await store.get_preferences(user_id=user.id)
    if existing is not None:
        return existing
    return AlertPreference(user_id=user.id, updated_at=datetime.now(UTC))


@router.put("/me/alert-preferences")
async def put_alert_preferences(
    body: AlertPreferenceRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> AlertPreference:
    if DeliveryChannelKind.EMAIL in body.channels and user.email:
        # Ticking 電子郵件 again is the user choosing to receive email, so a past unsubscribe is
        # undone (a bounce or spam complaint is not).
        await store.clear_unsubscribe(address=user.email)
    return await store.upsert_preferences(
        user_id=user.id,
        channels=body.channels,
        enabled_categories=body.enabled_categories,
        quiet_hours=body.quiet_hours,
        morning_digest=body.morning_digest,
        timezone=body.timezone,
    )


__all__ = ["router"]
