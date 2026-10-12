"""Usage counters/events and the Paddle subscription and webhook log."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.models._common import USERS_ID_FK as _USERS_ID_FK
from db.models._common import UTC_TIMESTAMP as _UTC


class UsageCounterRow(Base):
    """The authoritative per-user rolling counter behind daily-chat-limit enforcement.

    One row per `(user_id, counter, window_key)` — e.g. `('chat', '2026-08-05')` for the
    UTC-day chat count. The count is mutated only via an atomic
    `INSERT … ON CONFLICT DO UPDATE SET n = n + :delta` upsert, never a read-then-write, so two
    concurrent turns for the same user in the same window can never both slip past the cap. This
    lives in the app database (not Redis) on purpose: it is the source of truth a Free user's
    hard daily cap is enforced against, so it must survive a Redis flush — an ephemeral cache
    wipe must never silently re-grant spent quota.
    """

    __tablename__ = "usage_counters"

    user_id: Mapped[str] = mapped_column(primary_key=True)
    counter: Mapped[str] = mapped_column(primary_key=True)
    window_key: Mapped[str] = mapped_column(primary_key=True)
    n: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint("n >= 0", name="ck_usage_counters_nonneg"),
        Index("ix_usage_counters_user", "user_id"),
    )


class LlmUsageRow(Base):
    """One turn's LLM consumption on one (provider, model) - the source of the usage settings
    chart. Recorded from each provider's own per-response usage metadata (tokens everywhere, the
    charge inline on OpenRouter); provider usage-history APIs need admin credentials a user's
    inference key does not have, so this is the only data we can show. `byok` marks calls made with
    the user's own key."""

    __tablename__ = "llm_usage"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(index=True)
    turn_id: Mapped[str]
    provider: Mapped[str]
    model: Mapped[str]
    calls: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cached_tokens: Mapped[int] = mapped_column(default=0)
    # server_default so the additive upgrade can add this column to rows written before it existed
    cache_write_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    reasoning_tokens: Mapped[int] = mapped_column(default=0)
    cost_micros: Mapped[int] = mapped_column(default=0)
    byok: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "turn_id", "provider", "model", name="uq_llm_usage_user_turn_model"
        ),
        Index("ix_llm_usage_user_created", "user_id", "created_at"),
    )


class UsageEventRow(Base):
    """The durable per-turn usage rollup — the write-through target of a finished turn's
    `UsageAccumulator.snapshot()`. One row per completed turn: token/tool/cost totals for
    cost accounting and analytics. Distinct from `usage_counters` (the live enforcement gate):
    this is the append-only record of what a turn actually consumed."""

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(index=True)
    turn_id: Mapped[str]
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    tool_calls: Mapped[int] = mapped_column(default=0)
    finmind_calls: Mapped[int] = mapped_column(default=0)
    cost_micros: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        UniqueConstraint("user_id", "turn_id", name="uq_usage_events_user_turn"),
        Index("ix_usage_events_user_created", "user_id", "created_at"),
    )


class SubscriptionRow(Base):
    """The record of a user's Paddle subscription — the source of which `users.tier` is a derived
    cache. Written only by the billing webhook (and reconcile-on-read against the live Paddle
    API); the app never trusts a client-asserted tier. `updated_from_event_at` holds the
    `occurred_at` of the last webhook applied, so a late-delivered older event can be recognized
    and skipped rather than allowed to regress state. `scheduled_cancel_at` non-null means "the
    user asked to cancel, effective at that date" — the tier stays active until the actual
    `subscription.canceled` event fires."""

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    paddle_subscription_id: Mapped[str] = mapped_column(unique=True)
    paddle_customer_id: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str]
    price_id: Mapped[str]
    tier: Mapped[str]
    byok: Mapped[bool] = mapped_column(default=False)
    current_period_start: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    current_period_end: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    scheduled_cancel_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    updated_from_event_at: Mapped[datetime] = mapped_column(_UTC)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','trialing','past_due','paused','canceled')",
            name="ck_subscriptions_status",
        ),
        CheckConstraint("tier IN ('free','premium','pro')", name="ck_subscriptions_tier"),
    )


class BillingEventRow(Base):
    """The idempotency ledger for processed Paddle webhooks. The `paddle_event_id` unique
    constraint makes redelivery a no-op: a second insert of the same event collides and the
    handler treats it as already-processed. `raw_payload` is the verified envelope, kept for
    audit/debugging — it carries no card data (Paddle is the merchant of record)."""

    __tablename__ = "billing_events"

    id: Mapped[str] = mapped_column(primary_key=True)
    paddle_event_id: Mapped[str] = mapped_column(unique=True)
    event_type: Mapped[str]
    occurred_at: Mapped[datetime] = mapped_column(_UTC)
    processed_at: Mapped[datetime] = mapped_column(_UTC)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)

    __table_args__ = (Index("ix_billing_events_occurred", "occurred_at"),)
