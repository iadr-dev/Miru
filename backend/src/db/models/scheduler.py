"""Schedules, alerts, notification preferences/delivery targets and the inbox."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.models._common import MARKET_TW_US_CHECK as _MARKET_TW_US_CHECK
from db.models._common import USERS_ID_FK as _USERS_ID_FK
from db.models._common import UTC_TIMESTAMP as _UTC


class ScheduleJobRow(Base):
    """A user-authorized scheduled job. `read_only` is enforced `True` at the store write path
    (the scheduling carve-out forbids streaming/high-frequency polling); it exists as a column
    so the invariant is visible in the row, not just in code. `status='running'` is what lets a
    restarted worker detect a run orphaned by a crash."""

    __tablename__ = "schedule_jobs"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    trigger_type: Mapped[str]
    trigger_spec: Mapped[str]
    trigger_market: Mapped[str] = mapped_column(default="TW")
    skill_ref: Mapped[str]
    skill_inputs: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    read_only: Mapped[bool] = mapped_column(default=True)
    delivery_channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    quiet_start: Mapped[str | None] = mapped_column(default=None)
    quiet_end: Mapped[str | None] = mapped_column(default=None)
    daily_limit: Mapped[int | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_run: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    next_run: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    status: Mapped[str] = mapped_column(default="ok")
    # Why the last run was skipped or failed — shown on the schedule row instead of a bare status.
    status_reason: Mapped[str | None] = mapped_column(default=None)
    # The one persistent thread every run of this schedule appends to (created on the first run).
    thread_id: Mapped[str | None] = mapped_column(
        ForeignKey("threads.id", ondelete="SET NULL"), default=None
    )
    version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('cron','market_event')", name="ck_schedule_jobs_trigger_type"
        ),
        CheckConstraint("trigger_market IN ('TW','US')", name="ck_schedule_jobs_market"),
        # `= true` (not `= 1`) so the boolean compares to a boolean on Postgres, which rejects
        # `boolean = integer`; SQLite accepts the `true` literal against its integer-stored boolean.
        CheckConstraint("read_only = true", name="ck_schedule_jobs_read_only"),
        CheckConstraint(
            "status IN ('ok','running','skipped_quota','error')", name="ck_schedule_jobs_status"
        ),
        # The due-jobs worker scans only enabled jobs by next_run — a partial index.
        Index(
            "ix_schedule_jobs_due",
            "next_run",
            sqlite_where=text("enabled = 1"),
            postgresql_where=text("enabled"),
        ),
    )


class AlertRuleRow(Base):
    """An alert rule (modules/14 §3). Scope is one symbol *or* a watchlist group — never both,
    never neither (`ck_alert_rules_scope`). `channels`/`quiet_*`/`daily_limit` null-or-empty mean
    "inherit `alert_preferences`". `state` is the per-rule edge-trigger memory (`last_value`,
    `last_fired_at`, `fire_count`) that makes `crosses_*` and enter/leave real. `version` is the
    optimistic-lock counter for multi-device edits (409 on stale)."""

    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    symbol: Mapped[str | None] = mapped_column(default=None)
    market: Mapped[str | None] = mapped_column(default=None)
    watchlist_id: Mapped[str | None] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), default=None
    )
    # The group a per-stock rule was set up from (「逐檔設定」), for display only. Unlike
    # `watchlist_id` it does not scope the rule and does not cascade; it just labels where it came
    # from, and a deleted group simply leaves an unknown label.
    origin_watchlist_id: Mapped[str | None] = mapped_column(default=None)
    condition: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    trigger_mode: Mapped[str] = mapped_column(default="once_per_day")
    cooldown_seconds: Mapped[int | None] = mapped_column(default=None)
    state: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    auto_paused_reason: Mapped[str | None] = mapped_column(default=None)
    explain: Mapped[bool] = mapped_column(default=False)
    channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    quiet_start: Mapped[str | None] = mapped_column(default=None)
    quiet_end: Mapped[str | None] = mapped_column(default=None)
    daily_limit: Mapped[int | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_triggered: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(_MARKET_TW_US_CHECK, name="ck_alert_rules_market"),
        CheckConstraint(
            "(symbol IS NOT NULL AND market IS NOT NULL AND watchlist_id IS NULL)"
            " OR (symbol IS NULL AND market IS NULL AND watchlist_id IS NOT NULL)",
            name="ck_alert_rules_scope",
        ),
        CheckConstraint(
            "trigger_mode IN ('once','once_per_day','every_occurrence')",
            name="ck_alert_rules_trigger_mode",
        ),
        Index("ix_alert_rules_watchlist", "watchlist_id"),
        # The alert-engine scan — a partial index over only enabled rules.
        Index(
            "ix_alert_rules_scan",
            "market",
            "symbol",
            sqlite_where=text("enabled = 1"),
            postgresql_where=text("enabled"),
        ),
    )


class StrategySubscriptionRow(Base):
    """A user's 策略盯盤 subscription — one row per user. `watchlist_id` null means every stock on
    today's screen; a deleted group takes the subscription with it rather than silently widening
    it to the whole market."""

    __tablename__ = "strategy_subscriptions"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), unique=True, index=True
    )
    strategies: Mapped[list[str]] = mapped_column(JSON, default=list)
    watchlist_id: Mapped[str | None] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), default=None
    )
    morning_list: Mapped[bool] = mapped_column(default=True)
    channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    quiet_start: Mapped[str | None] = mapped_column(default=None)
    quiet_end: Mapped[str | None] = mapped_column(default=None)
    daily_limit: Mapped[int | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)


class AlertPreferenceRow(Base):
    __tablename__ = "alert_preferences"

    user_id: Mapped[str] = mapped_column(
        ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), primary_key=True
    )
    channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    quiet_start: Mapped[str | None] = mapped_column(default=None)
    quiet_end: Mapped[str | None] = mapped_column(default=None)
    morning_digest: Mapped[bool] = mapped_column(default=False)
    # The one persistent thread the daily 晨間摘要 is appended to (null until the first one).
    digest_thread_id: Mapped[str | None] = mapped_column(default=None)
    # IANA zone the quiet-hours window is evaluated in (v1 compared in UTC).
    timezone: Mapped[str] = mapped_column(default="Asia/Taipei")
    updated_at: Mapped[datetime] = mapped_column(_UTC)


class AlertEvalMarkRow(Base):
    """The reconciliation watermark: the latest `as_of` the alert evaluator has processed per
    `(capability, market)`. `data.updated` is best-effort, so the daily sweep compares the
    ingested store's latest partition to this mark and evaluates whatever it has not yet seen
    (modules/14). Idempotent re-evaluation is guarded by the notification dedupe key."""

    __tablename__ = "alert_eval_marks"

    capability: Mapped[str] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(primary_key=True)
    last_as_of: Mapped[str]
    evaluated_at: Mapped[datetime] = mapped_column(_UTC)


class DevicePushTokenRow(Base):
    __tablename__ = "device_push_tokens"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    platform: Mapped[str]
    token: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(_UTC)
    last_seen: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(
            "platform IN ('ios','android','web')", name="ck_device_push_tokens_platform"
        ),
        UniqueConstraint("user_id", "token", name="uq_device_push_tokens_user_token"),
    )


class ChannelLinkRow(Base):
    """A user's binding to one external messaging channel (Telegram today). A link starts as a
    one-time `link_token` the user sends to the bot; the webhook swaps it for the chat `address`.
    One row per `(user_id, channel)`."""

    __tablename__ = "channel_links"

    user_id: Mapped[str] = mapped_column(
        ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), primary_key=True
    )
    channel: Mapped[str] = mapped_column(primary_key=True)
    address: Mapped[str | None] = mapped_column(default=None)
    link_token: Mapped[str | None] = mapped_column(default=None, index=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    linked_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)

    __table_args__ = (
        CheckConstraint("channel IN ('telegram','line')", name="ck_channel_links_channel"),
        # One external chat belongs to one account (unlinked rows, with a NULL address, do not
        # collide).
        UniqueConstraint("channel", "address", name="uq_channel_links_address"),
    )


class EmailSuppressionRow(Base):
    """An address we must not email again: it hard-bounced or its owner marked a message as spam
    (recorded from Resend's webhook). Keyed by the lower-cased address; one row is enough — any
    later event for the same address is a no-op."""

    __tablename__ = "email_suppressions"

    address: Mapped[str] = mapped_column(primary_key=True)
    reason: Mapped[str]  # 'bounced' | 'complained' | 'unsubscribed'
    event_id: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(
            "reason IN ('bounced','complained','unsubscribed')", name="ck_email_suppressions_reason"
        ),
    )


class NotificationRow(Base):
    """A delivered/persisted notification. The `(user_id, dedupe_key)` unique constraint is the
    idempotent-delivery guard: at-least-once execution can attempt the same
    `(source_id, trigger_date, channel)` twice, but the second insert collides and is a no-op."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    source: Mapped[str]
    source_id: Mapped[str]
    channel: Mapped[str]
    dedupe_key: Mapped[str]
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    delivered_at: Mapped[datetime] = mapped_column(_UTC)
    read_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    # The outbox half of the row. An in-app entry is `sent` the moment it exists. An external one
    # starts `pending`, and becomes `sent`, or `failed` once it will not be retried; a transient
    # failure keeps it `pending` with the time of the next attempt, which the retry sweep reads.
    delivery_state: Mapped[str] = mapped_column(default="sent", server_default="sent")
    delivery_attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)

    __table_args__ = (
        CheckConstraint("source IN ('schedule','alert','digest')", name="ck_notifications_source"),
        CheckConstraint(
            "delivery_state IN ('pending','sent','failed')", name="ck_notifications_delivery_state"
        ),
        # The retry sweep scans only the rows still waiting to be delivered.
        Index(
            "ix_notifications_delivery_due",
            "next_attempt_at",
            sqlite_where=text("delivery_state = 'pending'"),
            postgresql_where=text("delivery_state = 'pending'"),
        ),
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe"),
        Index("ix_notifications_user_delivered", "user_id", "delivered_at"),
        # The cross-user quiet-hours release scan filters on delivered_at alone.
        Index("ix_notifications_delivered", "delivered_at"),
        # The unread-badge scan — a partial index over only unread rows.
        Index(
            "ix_notifications_unread",
            "user_id",
            sqlite_where=text("read_at IS NULL"),
            postgresql_where=text("read_at IS NULL"),
        ),
    )
