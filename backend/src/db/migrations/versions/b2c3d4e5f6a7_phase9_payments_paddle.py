"""phase9 payments paddle

Revision ID: b2c3d4e5f6a7
Revises: a1f2c3d4e5b6
Create Date: 2026-08-05 10:00:00.000000

Adds the Paddle billing schema: `subscriptions` (the record of a user's subscription, of which
`users.tier` is a derived cache — written only by the webhook and reconcile-on-read) and
`billing_events` (the idempotency ledger).

The `subscriptions.paddle_subscription_id` unique constraint keys the row to Paddle; the
`billing_events.paddle_event_id` unique constraint is the idempotency guard that makes webhook
redelivery a no-op. `subscriptions.updated_from_event_at` stores the `occurred_at` of the last
applied event so a late-delivered older event is recognized as out of order and skipped rather
than allowed to regress state. Dual-dialect: text UUID PKs, `text + CHECK` for `status`/`tier`
(never a native enum), JSON (not JSONB) for the raw payload — matching the earlier migrations.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1f2c3d4e5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("paddle_subscription_id", sa.String(), nullable=False),
        sa.Column("paddle_customer_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("price_id", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("byok", sa.Boolean(), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_cancel_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_from_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','trialing','past_due','paused','canceled')",
            name="ck_subscriptions_status",
        ),
        sa.CheckConstraint("tier IN ('free','premium','pro')", name="ck_subscriptions_tier"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "paddle_subscription_id", name="uq_subscriptions_paddle_subscription_id"
        ),
    )
    op.create_index("ix_subscriptions_user", "subscriptions", ["user_id"], unique=False)

    op.create_table(
        "billing_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("paddle_event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("paddle_event_id", name="uq_billing_events_paddle_event_id"),
    )
    op.create_index("ix_billing_events_occurred", "billing_events", ["occurred_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_billing_events_occurred", table_name="billing_events")
    op.drop_table("billing_events")
    op.drop_index("ix_subscriptions_user", table_name="subscriptions")
    op.drop_table("subscriptions")
