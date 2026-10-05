"""Domain models for the scheduler & alerts feature — schedule jobs, alert rules, per-user
alert preferences, delivered notifications, and registered native-push device tokens.

Every model is a frozen Pydantic v2 shape (the project convention for persisted/journaled
artifacts). The vocabulary enums here mirror the CHECK-constrained text columns their tables
carry, so the store and the migration agree on one set of allowed values.

Scheduling is a deliberately bounded amendment to this project's normal no-scheduler rule, so
the trigger vocabulary is intentionally coarse: post-close / pre-open market events, or a
weekday cron. There is no sub-daily / tick cadence value, by design — a high-frequency trigger
would violate the read-only, non-streaming bound the feature is permitted under.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.alerts import RuleCondition, TriggerMode
from schemas.enums import Market


class TriggerType(StrEnum):
    CRON = "cron"
    MARKET_EVENT = "market_event"


class MarketEvent(StrEnum):
    """The coarse, market-aware cadence points a `market_event` trigger may fire on. All are
    post-close / pre-open shaped — never intraday tick polling."""

    AFTER_CLOSE = "after_close"
    PRE_OPEN = "pre_open"
    MORNING_DIGEST = "morning_digest"


class JobStatus(StrEnum):
    OK = "ok"
    RUNNING = "running"  # in-flight — detects orphaned runs after a worker crash
    SKIPPED_QUOTA = "skipped_quota"
    ERROR = "error"


class DeliveryChannelKind(StrEnum):
    IN_APP = "in_app"
    LINE = "line"
    TELEGRAM = "telegram"
    EMAIL = "email"
    PUSH = "push"


class DevicePlatform(StrEnum):
    IOS = "ios"
    ANDROID = "android"
    WEB = "web"  # Web Push: `token` holds the JSON-encoded PushSubscription


class NotificationSource(StrEnum):
    SCHEDULE = "schedule"
    ALERT = "alert"
    DIGEST = "digest"


class QuietHours(BaseModel):
    """A per-user quiet window, stored as ISO `HH:MM` strings (portable — no DB `time` type
    reliance across dialects). A window that wraps past midnight (start > end) is honoured."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


# What a user who never saved notification preferences gets: silent from midnight to 08:00 in Taipei
# (GMT+8). The settings page shows the same values, so the default is visible and editable.
DEFAULT_TIMEZONE = "Asia/Taipei"
DEFAULT_QUIET_HOURS = QuietHours(start="00:00", end="08:00")


class ScheduleTrigger(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: TriggerType
    # For CRON: a weekday-cron expression string. For MARKET_EVENT: a `MarketEvent` value.
    spec: str = Field(min_length=1)
    market: Market = Market.TW


class ScheduleAction(BaseModel):
    """What a job runs: a governed skill by name plus its inputs. The skill is resolved against
    the same `SkillRegistry` interactive turns use — a scheduled run reuses a governed skill,
    never a bespoke code path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skill_ref: str = Field(min_length=1)
    inputs: dict[str, object] = Field(default_factory=dict)


class ScheduleJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    user_id: str
    trigger: ScheduleTrigger
    action: ScheduleAction
    delivery_channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = None
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None
    status: JobStatus = JobStatus.OK
    status_reason: str | None = None
    thread_id: str | None = None
    version: int = 0
    created_at: datetime
    updated_at: datetime


class SchedulePatch(BaseModel):
    """A partial user edit of a schedule. Only fields in `model_fields_set` are applied, so `None`
    clears the nullable ones (`quiet_hours`, `daily_limit`) while an omitted field is left alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool | None = None
    trigger: ScheduleTrigger | None = None
    action: ScheduleAction | None = None
    delivery_channels: tuple[DeliveryChannelKind, ...] | None = None
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = None


class AlertRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    user_id: str
    # Scope is a symbol (+market) XOR a watchlist group; the DB enforces the same XOR.
    symbol: str | None = None
    market: Market | None = None
    watchlist_id: str | None = None
    origin_watchlist_id: str | None = None
    condition: RuleCondition
    trigger_mode: TriggerMode = TriggerMode.ONCE_PER_DAY
    cooldown_seconds: int | None = None
    state: dict[str, object] = Field(default_factory=dict)
    expires_at: datetime | None = None
    auto_paused_reason: str | None = None
    explain: bool = False
    channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = None
    enabled: bool = True
    last_triggered: datetime | None = None
    version: int = 0
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _scope_is_symbol_xor_watchlist(self) -> AlertRule:
        has_symbol = self.symbol is not None and self.market is not None
        half_symbol = (self.symbol is None) != (self.market is None)
        if half_symbol or has_symbol == (self.watchlist_id is not None):
            raise ValueError("an alert rule needs a symbol+market or a watchlist_id, not both")
        return self


class AlertPatch(BaseModel):
    """A partial user edit of an alert rule. Only fields present in `model_fields_set` are applied,
    which is what lets `None` mean "clear" for the nullable ones (`expires_at`, `cooldown_seconds`,
    `quiet_hours`, `daily_limit`) while an omitted field means "leave alone". Scope (symbol/market/
    watchlist) is deliberately not editable — changing what a rule watches is delete + re-create."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool | None = None
    condition: RuleCondition | None = None
    trigger_mode: TriggerMode | None = None
    cooldown_seconds: int | None = None
    expires_at: datetime | None = None
    explain: bool | None = None
    channels: tuple[DeliveryChannelKind, ...] | None = None
    quiet_hours: QuietHours | None = None
    daily_limit: int | None = None


class BriefSymbols(BaseModel):
    """The user's own symbols the morning brief reports on, taken from their watchlist groups."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tw_stocks: tuple[str, ...] = ()
    us_stocks: tuple[str, ...] = ()
    etfs: tuple[str, ...] = ()


class AlertPreference(BaseModel):
    """One row per user — the default channels + digest + quiet-hours applied when a rule/job
    doesn't override them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    channels: tuple[DeliveryChannelKind, ...] = Field(default_factory=tuple)
    enabled_categories: tuple[str, ...] = Field(default_factory=tuple)
    quiet_hours: QuietHours | None = None
    morning_digest: bool = False
    digest_thread_id: str | None = None
    timezone: str = DEFAULT_TIMEZONE
    updated_at: datetime


class Notification(BaseModel):
    """One delivered (or in-app persisted) notification record. `dedupe_key` encodes
    `(source_id, trigger_date, channel)`; `(user_id, dedupe_key)` is unique, which is what makes
    redelivery a no-op under at-least-once execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    user_id: str
    source: NotificationSource
    source_id: str
    channel: DeliveryChannelKind
    dedupe_key: str
    payload: dict[str, object] = Field(default_factory=dict)
    delivered_at: datetime
    read_at: datetime | None = None


class DevicePushToken(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    user_id: str
    platform: DevicePlatform
    token: str
    created_at: datetime
    last_seen: datetime


class NotificationBadge(BaseModel):
    """The lightweight counts the mobile tab-island + top-bar bell dots read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unread_notifications: int = 0
    unread_threads: int = 0
    pending_alerts: int = 0


def dedupe_key_for(*, source_id: str, trigger_date: str, channel: DeliveryChannelKind) -> str:
    """Build the delivery dedupe key. `trigger_date` is an ISO date string so re-firing the same
    job/alert on the same day over the same channel collides (idempotent), while a genuinely new
    day's run is a distinct delivery."""
    return f"{source_id}:{trigger_date}:{channel.value}"


__all__ = [
    "AlertPatch",
    "AlertPreference",
    "AlertRule",
    "DeliveryChannelKind",
    "DevicePlatform",
    "DevicePushToken",
    "JobStatus",
    "MarketEvent",
    "Notification",
    "NotificationBadge",
    "NotificationSource",
    "QuietHours",
    "ScheduleAction",
    "ScheduleJob",
    "ScheduleTrigger",
    "TriggerMode",
    "TriggerType",
    "dedupe_key_for",
]
