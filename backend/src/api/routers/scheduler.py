"""Scheduler & alerts HTTP surface: schedules, alert rules, notifications, alert preferences,
and native-push device registration.

Every route is behind `require_user` and user-scoped — the store filters on `user_id`, so a
caller can never read or mutate another user's schedules. Alerts and schedules are open to every
signed-in user (modules/14) under uniform fair-use caps (`scheduler.caps`); what a tier gates is the
*data* a notification carries, not the feature. A creation request is validated against the
scheduling bounds (coarse cadence, real skill) and given a concrete `next_run` before it is stored
— without one the worker's `due_jobs` scan would never see it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import User
from accounts.store import UserNotFoundError, UserStore
from api.dependencies import (
    AppState,
    dispatch_validated_tool,
    get_session,
    get_state,
    get_user_store,
    require_user,
    tool_context_for,
)
from api.errors import ApiError
from config.settings import reveal
from scheduler.caps import MAX_ALERT_RULES, MAX_SCHEDULES
from scheduler.delivery.push_endpoint import subscription_endpoint_allowed
from scheduler.delivery.transport import HttpxTransport, OutboundTransport
from scheduler.models import (
    AlertPatch,
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
    SchedulePatch,
    ScheduleTrigger,
    TriggerMode,
)
from scheduler.store import (
    AlertRuleNotFoundError,
    ScheduleJobNotFoundError,
    SchedulerStore,
    SqlAlchemySchedulerStore,
    StaleVersionError,
)
from scheduler.triggers import UnresolvableTriggerError, next_run_after
from scheduler.unsubscribe import split_token, verify_token
from scheduler.validation import (
    InvalidTriggerError,
    UnknownSkillReferenceError,
    validate_action,
    validate_trigger,
)
from schemas.alerts import PER_STOCK_KINDS, RuleCondition, supports_market
from schemas.enums import Market
from schemas.schedule_templates import WATCHLIST_INPUT, WATCHLIST_SKILL

router = APIRouter(tags=["scheduler"])

# Numeric literal, matching api/errors.py — the `status.HTTP_422_*` constant name churns across
# Starlette versions and emits a deprecation warning; the code is stable.
_HTTP_422_UNPROCESSABLE = 422


def _store(session: AsyncSession = Depends(get_session)) -> SchedulerStore:
    return SqlAlchemySchedulerStore(session)


def _stale(what_id: str) -> ApiError:
    del what_id
    return ApiError(
        status_code=status.HTTP_409_CONFLICT,
        code="stale_version",
        message="This schedule was changed elsewhere. Reload it and try again.",
        retryable=False,
    )


def _cap_reached(what: str, limit: int) -> ApiError:
    return ApiError(
        status_code=status.HTTP_409_CONFLICT,
        code="cap_reached",
        message=f"You can keep at most {limit} {what}. Delete or pause one to add another.",
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
    """A partial edit. Only sent fields change; `null` clears `quiet_hours`/`daily_limit`.
    `expected_version` makes the edit refuse (409 `stale_version`) if another device saved first."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    trigger: ScheduleTrigger | None = None
    action: ScheduleAction | None = None
    delivery_channels: tuple[DeliveryChannelKind, ...] | None = None
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = Field(default=None, ge=1)
    expected_version: int | None = Field(default=None, ge=0)

    def to_patch(self) -> SchedulePatch:
        sent = self.model_fields_set - {"expected_version"}
        return SchedulePatch.model_validate({name: getattr(self, name) for name in sent})


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    body: CreateScheduleRequest,
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> ScheduleJob:
    if await store.count_jobs(user_id=user.id) >= MAX_SCHEDULES:
        raise _cap_reached("schedules", MAX_SCHEDULES)
    await _check_watchlist_scope(body.action, user, store)
    try:
        validate_trigger(body.trigger)
        validate_action(body.action, state.skill_registry)
        next_run = next_run_after(body.trigger, datetime.now(UTC))
    except (InvalidTriggerError, UnresolvableTriggerError) as exc:
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
        next_run=next_run,
    )


@router.get("/schedules")
async def list_schedules(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> dict[str, list[ScheduleJob]]:
    return {"schedules": await store.list_jobs(user_id=user.id)}


@router.patch("/schedules/{job_id}")
async def update_schedule(
    job_id: str,
    body: UpdateScheduleRequest,
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> ScheduleJob:
    patch = body.to_patch()
    if "action" in patch.model_fields_set and patch.action is not None:
        await _check_watchlist_scope(patch.action, user, store)
    try:
        current = await store.get_job(user_id=user.id, job_id=job_id)
        next_run = _resolved_next_run(patch, current, state)
        return await store.update_job(
            user_id=user.id,
            job_id=job_id,
            patch=patch,
            next_run=next_run,
            expected_version=body.expected_version,
        )
    except ScheduleJobNotFoundError as exc:
        raise _not_found("schedule_not_found", f"Schedule {job_id} not found.") from exc
    except StaleVersionError as exc:
        raise _stale(job_id) from exc
    except (InvalidTriggerError, UnresolvableTriggerError) as exc:
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


async def _check_watchlist_scope(action: ScheduleAction, user: User, store: SchedulerStore) -> None:
    """A watchlist-scoped schedule must point at one of the caller's own lists, and only the
    multi-stock briefing can expand a list."""
    watchlist_id = action.inputs.get(WATCHLIST_INPUT)
    if watchlist_id is None:
        return
    if action.skill_ref != WATCHLIST_SKILL:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="watchlist_scope_unsupported",
            message="Only the pre-open briefing can follow a watchlist.",
            retryable=False,
        )
    if not isinstance(watchlist_id, str) or not await store.watchlist_owned_by(
        user_id=user.id, watchlist_id=watchlist_id
    ):
        raise _not_found("watchlist_not_found", f"Watchlist {watchlist_id} not found.")


def _resolved_next_run(
    patch: SchedulePatch, current: ScheduleJob, state: AppState
) -> datetime | None:
    """Validate an edit's trigger/action and resolve the job's new `next_run`. A new trigger, or
    re-enabling a job, recomputes it from now; any other edit leaves the schedule's timing alone."""
    given = patch.model_fields_set
    if "action" in given and patch.action is not None:
        validate_action(patch.action, state.skill_registry)
    trigger = patch.trigger if "trigger" in given and patch.trigger is not None else None
    if trigger is not None:
        validate_trigger(trigger)
    if trigger is None and not (patch.enabled is True and not current.enabled):
        return None
    return next_run_after(trigger or current.trigger, datetime.now(UTC))


@router.delete("/schedules/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    job_id: str,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
    try:
        await store.delete_job(user_id=user.id, job_id=job_id)
    except ScheduleJobNotFoundError as exc:
        raise _not_found("schedule_not_found", f"Schedule {job_id} not found.") from exc


# ---- alerts --------------------------------------------------------------------------------


_ALERT_MARKETS = frozenset({Market.TW, Market.US})
_DEFAULT_ALERT_LIFETIME_DAYS = 90


class CreateAlertRequest(BaseModel):
    # Scope: one symbol (+market) XOR one watchlist group.
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    market: Market | None = None
    watchlist_id: str | None = None
    condition: RuleCondition
    trigger_mode: TriggerMode = TriggerMode.ONCE_PER_DAY
    cooldown_seconds: int | None = Field(default=None, ge=0)
    # Days until the rule auto-expires; null = never. Defaults to 90 so forgotten rules lapse.
    expires_in_days: int | None = Field(default=_DEFAULT_ALERT_LIFETIME_DAYS, ge=1, le=3650)
    explain: bool = False
    # Empty channels / null quiet_hours / null daily_limit inherit the user's alert preferences.
    channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = Field(default=None, ge=1)

    @field_validator("symbol")
    @classmethod
    def _normalise_symbol(cls, value: str | None) -> str | None:
        """Tickers are stored upper-case and trimmed, because the ingested partitions key on the
        exchange ticker ('AAPL', '00631L'): an alert saved as 'aapl' would never match a row."""
        return value.strip().upper() if value is not None else None

    @model_validator(mode="after")
    def _one_scope(self) -> CreateAlertRequest:
        has_symbol = self.symbol is not None and self.market is not None
        half = (self.symbol is None) != (self.market is None)
        if half or has_symbol == (self.watchlist_id is not None):
            raise ValueError("provide symbol+market or watchlist_id, not both and not neither")
        return self


class UpdateAlertRequest(BaseModel):
    """A partial edit. Send only what changed; `null` clears a nullable field (`expires_in_days`,
    `cooldown_seconds`, `quiet_hours`, `daily_limit`). `expected_version` is the rule's `version`
    as the client last saw it — a mismatch is a 409 so edits from two devices never silently
    overwrite each other. The rule's scope (symbol / market / watchlist) cannot be edited, and
    unknown fields are rejected (422) rather than silently ignored."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    condition: RuleCondition | None = None
    trigger_mode: TriggerMode | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    explain: bool | None = None
    channels: tuple[DeliveryChannelKind, ...] | None = None
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = Field(default=None, ge=1)

    def to_patch(self) -> AlertPatch:
        """Translate to the store's patch, preserving present-vs-omitted (so an explicit `null`
        clears). `expires_in_days` becomes an absolute `expires_at` measured from now."""
        given = self.model_fields_set - {"expected_version", "expires_in_days"}
        values: dict[str, object] = {name: getattr(self, name) for name in given}
        if "expires_in_days" in self.model_fields_set:
            values["expires_at"] = (
                datetime.now(UTC) + timedelta(days=self.expires_in_days)
                if self.expires_in_days is not None
                else None
            )
        return AlertPatch.model_validate(values)


@router.post("/alerts", status_code=status.HTTP_201_CREATED)
async def create_alert(
    body: CreateAlertRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> AlertRule:
    if await store.count_alerts(user_id=user.id) >= MAX_ALERT_RULES:
        raise _cap_reached("alert rules", MAX_ALERT_RULES)
    if body.market is not None and not supports_market(body.condition, body.market):
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="template_market_mismatch",
            message=f"'{body.condition.kind}' alerts exist for the TW market only.",
            retryable=False,
        )
    if body.watchlist_id is not None and body.condition.kind in PER_STOCK_KINDS:
        # One price level cannot mean the same thing for every stock in a group (2,475 vs 185), so
        # a price alert must be set per stock — see `POST /alerts/batch`.
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="template_scope_mismatch",
            message="Price-level alerts are set per stock, not for a whole watchlist group.",
            retryable=False,
        )
    if body.market is not None and body.market not in _ALERT_MARKETS:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="invalid_market",
            message="Alerts support the TW and US markets.",
            retryable=False,
        )
    if body.watchlist_id is not None and not await store.watchlist_owned_by(
        user_id=user.id, watchlist_id=body.watchlist_id
    ):
        raise _not_found("watchlist_not_found", f"Watchlist {body.watchlist_id} not found.")
    expires_at = (
        datetime.now(UTC) + timedelta(days=body.expires_in_days)
        if body.expires_in_days is not None
        else None
    )
    return await store.create_alert(
        user_id=user.id,
        symbol=body.symbol,
        market=body.market,
        watchlist_id=body.watchlist_id,
        condition=body.condition,
        trigger_mode=body.trigger_mode,
        cooldown_seconds=body.cooldown_seconds,
        expires_at=expires_at,
        explain=body.explain,
        channels=body.channels,
        quiet_hours=body.quiet_hours,
        daily_limit=body.daily_limit,
    )


_MAX_BATCH = 30


class BatchAlertItem(BaseModel):
    """One stock's own rule in a per-stock batch."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    market: Market
    condition: RuleCondition

    @field_validator("symbol")
    @classmethod
    def _normalise_symbol(cls, value: str) -> str:
        return value.strip().upper()


class BatchCreateAlertsRequest(BaseModel):
    """Several per-stock rules created together (「逐檔設定」): each item has its own stock and
    its own level; frequency, lifetime and channels are shared."""

    model_config = ConfigDict(extra="forbid")

    items: tuple[BatchAlertItem, ...] = Field(min_length=1, max_length=_MAX_BATCH)
    origin_watchlist_id: str | None = None
    trigger_mode: TriggerMode = TriggerMode.ONCE_PER_DAY
    cooldown_seconds: int | None = Field(default=None, ge=0)
    expires_in_days: int | None = Field(default=_DEFAULT_ALERT_LIFETIME_DAYS, ge=1, le=3650)
    channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _no_duplicate_stock(self) -> BatchCreateAlertsRequest:
        seen = {(i.symbol, i.market) for i in self.items}
        if len(seen) != len(self.items):
            raise ValueError("each stock may appear once in a batch")
        return self


@router.post("/alerts/batch", status_code=status.HTTP_201_CREATED)
async def create_alerts_batch(
    body: BatchCreateAlertsRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> dict[str, list[AlertRule]]:
    """All-or-nothing: every item is validated and the fair-use cap is checked for the whole batch
    before anything is written, and the request runs in one transaction."""
    if await store.count_alerts(user_id=user.id) + len(body.items) > MAX_ALERT_RULES:
        raise _cap_reached("alert rules", MAX_ALERT_RULES)
    for item in body.items:
        if item.market not in _ALERT_MARKETS:
            raise ApiError(
                status_code=_HTTP_422_UNPROCESSABLE,
                code="invalid_market",
                message="Alerts support the TW and US markets.",
                retryable=False,
            )
        if not supports_market(item.condition, item.market):
            raise ApiError(
                status_code=_HTTP_422_UNPROCESSABLE,
                code="template_market_mismatch",
                message=f"'{item.condition.kind}' alerts exist for the TW market only.",
                retryable=False,
            )
    if body.origin_watchlist_id is not None and not await store.watchlist_owned_by(
        user_id=user.id, watchlist_id=body.origin_watchlist_id
    ):
        raise _not_found("watchlist_not_found", f"Watchlist {body.origin_watchlist_id} not found.")
    expires_at = (
        datetime.now(UTC) + timedelta(days=body.expires_in_days)
        if body.expires_in_days is not None
        else None
    )
    created = [
        await store.create_alert(
            user_id=user.id,
            symbol=item.symbol,
            market=item.market,
            origin_watchlist_id=body.origin_watchlist_id,
            condition=item.condition,
            trigger_mode=body.trigger_mode,
            cooldown_seconds=body.cooldown_seconds,
            expires_at=expires_at,
            channels=body.channels,
        )
        for item in body.items
    ]
    return {"alerts": created}


@router.get("/alerts/symbol-check")
async def check_alert_symbol(
    symbol: str = Query(min_length=1, max_length=20),
    market: Market = Query(),
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
) -> dict[str, bool]:
    """Does this ticker have any price data? A pre-flight the alert form calls so a typo
    (`APPLE` for `AAPL`) is flagged *before* a rule is saved that could never fire. It only
    reports — creation itself is never blocked, since a brand-new listing may genuinely have no
    bar yet. `checked=False` means the lookup itself failed, which says nothing about the ticker."""
    try:
        result = await dispatch_validated_tool(
            state,
            "get_price_quote",
            {"id": symbol.strip().upper(), "market": market.value},
            tool_context_for(user, state),
        )
    except Exception:
        return {"checked": False, "exists": False}
    return {"checked": True, "exists": result.get("bar") is not None}


@router.get("/alerts")
async def list_alerts(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> dict[str, list[AlertRule]]:
    return {"alerts": await store.list_alerts(user_id=user.id)}


@router.patch("/alerts/{alert_id}")
async def update_alert(
    alert_id: str,
    body: UpdateAlertRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> AlertRule:
    try:
        return await store.update_alert(
            user_id=user.id,
            alert_id=alert_id,
            patch=body.to_patch(),
            expected_version=body.expected_version,
        )
    except StaleVersionError as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="stale_version",
            message="This alert was changed elsewhere. Reload it and try again.",
            retryable=False,
        ) from exc
    except AlertRuleNotFoundError as exc:
        raise _not_found("alert_not_found", f"Alert {alert_id} not found.") from exc


@router.delete("/alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(
    alert_id: str,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
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


# ---- alert preferences ---------------------------------------------------------------------


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


# ---- external channel links (Telegram) ------------------------------------------------------

_LINK_TOKEN_TTL = timedelta(minutes=10)
_TELEGRAM = "telegram"
_LINE = "line"


class ChannelStatus(BaseModel):
    """Which external channels can actually reach this user right now."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    email: bool
    push: bool
    line: bool
    telegram: bool
    telegram_available: bool
    # LINE: `line` = a message can be pushed now; `line_linked` = the user linked this account by
    # sending the official account a code; `line_available` = the server has the bot credentials.
    line_linked: bool = False
    line_available: bool = False
    # Why this account's address is not emailed: 'bounced' | 'complained' | 'unsubscribed'.
    email_blocked: str | None = None
    # Browser notifications: the VAPID public key the browser subscribes with (null = not set up),
    # and the LINE official account's add-friend link (null = not set up).
    web_push_public_key: str | None = None
    line_add_friend_url: str | None = None


def _real_from_address(value: str | None) -> bool:
    """A from-address that can actually send: present, and not on a reserved `.example` domain (the
    placeholder in `.env.example`), which no mail provider will ever verify."""
    if not value or "@" not in value:
        return False
    domain = value.rsplit("@", 1)[1].strip(" >").lower()
    return bool(domain) and not domain.endswith(".example")


@router.get("/me/channels")
async def get_channel_status(
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> ChannelStatus:
    settings = state.settings
    blocked = await store.email_suppression_reason(address=user.email) if user.email else None
    email_ready = bool(
        user.email
        and settings.resend_api_key
        and _real_from_address(settings.email_from_address)
        and blocked is None
    )
    return ChannelStatus(
        email=email_ready,
        email_blocked=blocked,
        push=bool(settings.vapid_private_key and await store.list_devices(user_id=user.id)),
        line=bool(
            settings.line_messaging_channel_access_token
            and await store.linked_line_user_id(user_id=user.id)
        ),
        telegram=await store.channel_address(user_id=user.id, channel=_TELEGRAM) is not None,
        line_linked=await store.channel_address(user_id=user.id, channel=_LINE) is not None,
        line_available=bool(
            settings.line_messaging_channel_access_token and settings.line_messaging_channel_secret
        ),
        telegram_available=bool(settings.telegram_bot_token and settings.telegram_bot_username),
        web_push_public_key=(settings.vapid_public_key if settings.vapid_private_key else None),
        line_add_friend_url=(
            f"https://line.me/R/ti/p/{settings.line_bot_basic_id}"
            if settings.line_messaging_channel_access_token and settings.line_bot_basic_id
            else None
        ),
    )


class TelegramLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    expires_in_seconds: int


@router.post("/me/channels/telegram/link")
async def start_telegram_link(
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> TelegramLink:
    """Issue a one-time link: the user opens it, taps Start in the bot, and the webhook below binds
    that chat to their account. Nothing is linked until they do."""
    username = state.settings.telegram_bot_username
    if not (state.settings.telegram_bot_token and username):
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="channel_unavailable",
            message="Telegram delivery is not set up on this server.",
            retryable=False,
        )
    token = secrets.token_urlsafe(16)
    await store.start_channel_link(
        user_id=user.id,
        channel=_TELEGRAM,
        token=token,
        expires_at=datetime.now(UTC) + _LINK_TOKEN_TTL,
    )
    return TelegramLink(
        # `.env` may hold the handle as shown in Telegram ("@name"); the deep link wants it bare.
        url=f"https://t.me/{username.removeprefix('@')}?start={token}",
        expires_in_seconds=int(_LINK_TOKEN_TTL.total_seconds()),
    )


@router.delete("/me/channels/telegram", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_telegram(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
    await store.unlink_channel(user_id=user.id, channel=_TELEGRAM)


_START_PREFIX = "/start "

_LINE_CODE = re.compile(r"\b([A-Z2-7]{8})\b")
_LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
_LINK_HELP = "請到 Miru 的「設定 → 通知」按「連結 LINE」，取得驗證碼後傳給我，就能收到提醒通知。"


class LineLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    add_friend_url: str | None
    expires_in_seconds: int


@router.post("/me/channels/line/link")
async def start_line_link(
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> LineLink:
    """Issue a one-time code. The user adds the official account and sends it the code; the
    webhook below binds the sender's LINE id to this account. Nothing is linked until they do."""
    settings = state.settings
    if not (
        settings.line_messaging_channel_access_token and settings.line_messaging_channel_secret
    ):
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="channel_unavailable",
            message="LINE delivery is not set up on this server.",
            retryable=False,
        )
    code = base64.b32encode(secrets.token_bytes(5)).decode()  # 8 chars of A-Z2-7, 40 bits
    await store.start_channel_link(
        user_id=user.id,
        channel=_LINE,
        token=code,
        expires_at=datetime.now(UTC) + _LINK_TOKEN_TTL,
    )
    return LineLink(
        code=code,
        add_friend_url=(
            f"https://line.me/R/ti/p/{settings.line_bot_basic_id}"
            if settings.line_bot_basic_id
            else None
        ),
        expires_in_seconds=int(_LINK_TOKEN_TTL.total_seconds()),
    )


@router.delete("/me/channels/line", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_line(
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
    await store.unlink_channel(user_id=user.id, channel=_LINE)


_RESEND_TOLERANCE_SECONDS = 300

_UNSUB_PAGE = (
    '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>{title}</title></head>"
    '<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;'
    'padding:0 1rem">'
    "<h1>{title}</h1><p>{body}</p>{form}</body></html>"
)


async def _unsubscribe_target(
    token: str, state: AppState, users: UserStore
) -> tuple[User, str] | None:
    """The account a valid unsubscribe token names and its email, or None if it is invalid."""
    secret = reveal(state.settings.jwt_secret_key)
    parts = split_token(token)
    if parts is None or not secret:
        return None
    try:
        user = await users.get(user_id=parts[0])
    except UserNotFoundError:
        return None
    if not user.email or not verify_token(secret, token, user.id, user.email):
        return None
    return user, user.email


@router.get("/email/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_page(
    token: str = Query(min_length=3, max_length=200),
    state: AppState = Depends(get_state),
    users: UserStore = Depends(get_user_store),
    store: SchedulerStore = Depends(_store),
) -> HTMLResponse:
    """A confirmation page, not the action: mail scanners open links, so a GET must not unsubscribe
    anyone. The button posts to the same URL. An address that already unsubscribed sees the
    opposite button (resubscribe) instead."""
    target = await _unsubscribe_target(token, state, users)
    if target is None:
        return HTMLResponse(
            _UNSUB_PAGE.format(title="連結無效", body="這個取消訂閱連結無效或已過期。", form=""),
            status_code=400,
        )
    if await store.email_suppression_reason(address=target[1]) == "unsubscribed":
        return _unsubscribed_page(token)
    form = (
        f'<form method="post" action="/api/v1/email/unsubscribe?token={html.escape(token)}">'
        '<button type="submit" style="padding:.6rem 1rem">確認取消電子郵件通知</button></form>'
    )
    return HTMLResponse(
        _UNSUB_PAGE.format(
            title="取消電子郵件通知", body="確認後，我們不會再寄提醒信給你。", form=form
        )
    )


@router.post("/email/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_action(
    token: str = Query(min_length=3, max_length=200),
    state: AppState = Depends(get_state),
    users: UserStore = Depends(get_user_store),
    store: SchedulerStore = Depends(_store),
) -> HTMLResponse:
    """The one-click unsubscribe (RFC 8058: mail clients POST `List-Unsubscribe=One-Click` here) and
    the confirmation button's target. The address stops receiving email; the user can turn it back
    on by ticking 電子郵件 in settings."""
    target = await _unsubscribe_target(token, state, users)
    if target is None:
        return HTMLResponse(
            _UNSUB_PAGE.format(title="連結無效", body="這個取消訂閱連結無效或已過期。", form=""),
            status_code=400,
        )
    await store.suppress_email(address=target[1], reason="unsubscribed")
    return _unsubscribed_page(token)


@router.post("/email/resubscribe", response_class=HTMLResponse)
async def resubscribe_action(
    token: str = Query(min_length=3, max_length=200),
    state: AppState = Depends(get_state),
    users: UserStore = Depends(get_user_store),
    store: SchedulerStore = Depends(_store),
) -> HTMLResponse:
    """Undo an unsubscribe with the same signed link. Only an *unsubscribe* is undone — a bounce or
    spam complaint stays (`clear_unsubscribe`). Email also needs to be ticked in settings, so the
    page points there when the channel is off."""
    target = await _unsubscribe_target(token, state, users)
    if target is None:
        return HTMLResponse(
            _UNSUB_PAGE.format(title="連結無效", body="這個連結無效或已過期。", form=""),
            status_code=400,
        )
    await store.clear_unsubscribe(address=target[1])
    settings_url = f"{state.settings.frontend_base_url.rstrip('/')}/zh-TW/settings/notifications"
    return HTMLResponse(
        _UNSUB_PAGE.format(
            title="已重新訂閱電子郵件通知",
            body="之後會再寄提醒信給你。",
            form=(
                f'<p><a href="{html.escape(settings_url)}">前往通知設定確認電子郵件已勾選</a></p>'
                '<form method="post" action="/api/v1/email/unsubscribe'
                f'?token={html.escape(token)}">'
                '<button type="submit" style="padding:.6rem 1rem">取消電子郵件通知</button></form>'
            ),
        )
    )


def _unsubscribed_page(token: str) -> HTMLResponse:
    return HTMLResponse(
        _UNSUB_PAGE.format(
            title="已取消電子郵件通知",
            body="之後不會再寄提醒信給你。改變心意了？",
            form=(
                '<form method="post" action="/api/v1/email/resubscribe'
                f'?token={html.escape(token)}">'
                '<button type="submit" style="padding:.6rem 1rem">重新訂閱</button></form>'
            ),
        )
    )


def _resend_signature_ok(secret: str, headers: dict[str, str], body: bytes) -> bool:
    """Resend signs webhooks with Svix: `svix-signature` holds one or more `v1,<base64>` values,
    each the HMAC-SHA256 of `"{svix-id}.{svix-timestamp}.{body}"` under the secret's bytes (the
    base64 after `whsec_`). A stale timestamp is refused so a captured request is not replayed."""
    msg_id, stamp, provided = (
        headers.get("svix-id"),
        headers.get("svix-timestamp"),
        headers.get("svix-signature"),
    )
    if not (msg_id and stamp and provided):
        return False
    try:
        if abs(datetime.now(UTC).timestamp() - int(stamp)) > _RESEND_TOLERANCE_SECONDS:
            return False
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except ValueError:
        return False
    expected = base64.b64encode(
        hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    candidates = [part.split(",", 1)[1] for part in provided.split() if "," in part]
    return any(hmac.compare_digest(c.encode(), expected.encode()) for c in candidates)


@router.post("/webhooks/resend")
async def resend_webhook(
    request: Request,
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
) -> dict[str, int]:
    """Resend reports what happened to each email. Three outcomes matter here: a hard bounce
    (`email.bounced`, type Permanent), a spam complaint (`email.complained`) and Resend's own
    `email.suppressed`. The address is recorded and never emailed again. A soft bounce, delivery
    and open events are acknowledged and ignored. The request must carry a valid Svix signature;
    with no secret configured every request is refused."""
    secret = reveal(state.settings.resend_webhook_secret)
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not secret or not _resend_signature_ok(secret, headers, body):
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            message="Bad webhook signature.",
            retryable=False,
        )
    try:
        event = cast("dict[str, object]", json.loads(body or b"{}"))
    except ValueError:
        return {"suppressed": 0}
    kind = event.get("type")
    data = event.get("data")
    if not isinstance(data, dict):
        return {"suppressed": 0}
    payload = cast("dict[str, object]", data)
    bounce = payload.get("bounce")
    hard_bounce = (
        kind == "email.bounced"
        and isinstance(bounce, dict)
        and str(cast("dict[str, object]", bounce).get("type", "")).lower() == "permanent"
    )
    # `email.suppressed`: Resend itself declined to send because the address is already on its own
    # suppression list — it is just as unreachable for us, so it is recorded the same way.
    if not (hard_bounce or kind in ("email.complained", "email.suppressed")):
        return {"suppressed": 0}
    reason = "complained" if kind == "email.complained" else "bounced"
    recipients = payload.get("to")
    count = 0
    for address in cast("list[object]", recipients) if isinstance(recipients, list) else []:
        if isinstance(address, str) and address.strip():
            await store.suppress_email(
                address=address, reason=reason, event_id=str(payload.get("email_id") or "") or None
            )
            count += 1
    return {"suppressed": count}


def get_line_transport() -> OutboundTransport:
    """The transport the LINE webhook replies with (overridden in tests)."""
    return HttpxTransport()


async def _line_reply(
    transport: OutboundTransport, token: str, reply_token: str, text: str
) -> None:
    """Best effort: a failed reply must never turn the webhook into an error (LINE would retry)."""
    await transport.post_json(
        _LINE_REPLY_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        body={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
    )


@router.post("/channels/line/webhook")
async def line_webhook(
    request: Request,
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
    transport: OutboundTransport = Depends(get_line_transport),
    signature: str | None = Header(default=None, alias="X-Line-Signature"),
) -> dict[str, int]:
    """LINE calls this for every event on the official account (the URL is registered in the LINE
    console). The `X-Line-Signature` header is base64(HMAC-SHA256(channel secret, raw body)); a
    request without a valid one is refused before the body is read as JSON. Handled events: a
    message carrying a link code binds the sender; `follow` sends instructions; `unfollow` drops
    the link (the user blocked the account, so pushes would fail)."""
    settings = state.settings
    secret, token = (
        reveal(settings.line_messaging_channel_secret),
        reveal(settings.line_messaging_channel_access_token),
    )
    body = await request.body()
    expected = (
        base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        if secret
        else None
    )
    if (
        expected is None
        or signature is None
        or not hmac.compare_digest(signature.encode(), expected.encode())
    ):
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            message="Bad webhook signature.",
            retryable=False,
        )
    try:
        payload = cast("dict[str, object]", json.loads(body or b"{}"))
    except ValueError:
        return {"handled": 0}
    events = payload.get("events")
    handled = 0
    for raw in cast("list[object]", events) if isinstance(events, list) else []:
        if not isinstance(raw, dict):
            continue
        event = cast("dict[str, object]", raw)
        source = event.get("source")
        line_user = (
            cast("dict[str, object]", source).get("userId") if isinstance(source, dict) else None
        )
        reply_token = event.get("replyToken")
        kind = event.get("type")
        if not isinstance(line_user, str):
            continue
        if kind == "unfollow":
            await store.unlink_line_user(line_user_id=line_user)
            handled += 1
        elif kind in ("follow", "message"):
            message = event.get("message")
            text = (
                cast("dict[str, object]", message).get("text")
                if isinstance(message, dict)
                else None
            )
            match = _LINE_CODE.search(text.upper()) if isinstance(text, str) else None
            linked = False
            if match is not None:
                user_id = await store.complete_channel_link(
                    channel=_LINE, token=match.group(1), address=line_user, now=datetime.now(UTC)
                )
                linked = user_id is not None
            if token and isinstance(reply_token, str):
                reply = "已連結完成，之後的提醒會傳到這裡。" if linked else _LINK_HELP
                await _line_reply(transport, token, reply_token, reply)
            handled += 1
    return {"handled": handled}


@router.post("/webhooks/telegram")
async def telegram_webhook(
    update: dict[str, object],
    state: AppState = Depends(get_state),
    store: SchedulerStore = Depends(_store),
    secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict[str, bool]:
    """Telegram calls this for every message to the bot. Only `/start <token>` matters: it binds
    the sender's chat to the account that issued the token. The secret header proves the call is
    Telegram's; anything else is ignored with a 200 so Telegram does not retry."""
    expected = reveal(state.settings.telegram_webhook_secret)
    if (
        not expected
        or secret is None
        or not hmac.compare_digest(secret.encode(), expected.encode())
    ):
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            message="Bad webhook secret.",
            retryable=False,
        )
    message = update.get("message")
    if not isinstance(message, dict):
        return {"linked": False}
    text = cast("dict[str, object]", message).get("text")
    chat = cast("dict[str, object]", message).get("chat")
    chat_id = cast("dict[str, object]", chat).get("id") if isinstance(chat, dict) else None
    if not (isinstance(text, str) and text.startswith(_START_PREFIX) and chat_id is not None):
        return {"linked": False}
    user_id = await store.complete_channel_link(
        channel=_TELEGRAM,
        token=text[len(_START_PREFIX) :].strip(),
        address=str(chat_id),
        now=datetime.now(UTC),
    )
    return {"linked": user_id is not None}


# ---- devices -------------------------------------------------------------------------------


class RegisterDeviceRequest(BaseModel):
    platform: DevicePlatform
    # A web `PushSubscription` serialised to JSON (endpoint + keys) is well over 512 bytes.
    token: str = Field(min_length=1, max_length=4096)


@router.post("/me/devices", status_code=status.HTTP_201_CREATED)
async def register_device(
    body: RegisterDeviceRequest,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> DevicePushToken:
    # A web token is a push-service URL the server will later POST to, so only real push services
    # are accepted (https + an allow-listed host) — otherwise it is an SSRF primitive.
    if body.platform is DevicePlatform.WEB and not subscription_endpoint_allowed(body.token):
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="invalid_push_subscription",
            message="That is not a browser push subscription from a supported push service.",
            retryable=False,
        )
    return await store.register_device(user_id=user.id, platform=body.platform, token=body.token)


@router.delete("/me/devices/{token:path}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device(
    token: str,
    user: User = Depends(require_user),
    store: SchedulerStore = Depends(_store),
) -> None:
    await store.unregister_device(user_id=user.id, token=token)


__all__ = ["router"]
