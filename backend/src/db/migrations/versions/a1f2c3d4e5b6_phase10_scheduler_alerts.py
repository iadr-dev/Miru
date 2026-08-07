"""phase10 scheduler alerts

Revision ID: a1f2c3d4e5b6
Revises: 8c5e740b8f90
Create Date: 2026-08-04 17:30:00.000000

Adds the Phase-10 scheduler/alerts schema: `schedule_jobs` (user-authorized jobs — with a
`running` status value so a restarted worker can detect a run orphaned by a crash, and a
`read_only` CHECK pinned true since the scheduling carve-out forbids streaming/high-frequency
polling), `alert_rules`, `alert_preferences`, `device_push_tokens`, and `notifications`.

The `notifications` `UNIQUE (user_id, dedupe_key)` constraint is the idempotent-delivery guard
that makes at-least-once execution safe: `dedupe_key = (source_id, trigger_date, channel)`, so a
redelivered notification collides and is a no-op.

Dialect-aware: partial indexes (due-jobs scan on `next_run WHERE enabled`, alert-engine scan on
`(market, symbol) WHERE enabled`, unread-badge on `WHERE read_at IS NULL`) use the
`sqlite_where`/`postgresql_where` kwargs, matching the pattern the earlier migrations used.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f2c3d4e5b6"
down_revision: str | None = "8c5e740b8f90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedule_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("trigger_type", sa.String(), nullable=False),
        sa.Column("trigger_spec", sa.String(), nullable=False),
        sa.Column("trigger_market", sa.String(), nullable=False),
        sa.Column("skill_ref", sa.String(), nullable=False),
        sa.Column("skill_inputs", sa.JSON(), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False),
        sa.Column("delivery_channels", sa.JSON(), nullable=False),
        sa.Column("quiet_start", sa.String(), nullable=True),
        sa.Column("quiet_end", sa.String(), nullable=True),
        sa.Column("daily_limit", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "trigger_type IN ('cron','market_event')", name="ck_schedule_jobs_trigger_type"
        ),
        sa.CheckConstraint("trigger_market IN ('TW','US')", name="ck_schedule_jobs_market"),
        # `= true` (not `= 1`) so the boolean compares to a boolean on Postgres, which rejects
        # `boolean = integer`; SQLite accepts the `true` literal against its integer-stored boolean.
        sa.CheckConstraint("read_only = true", name="ck_schedule_jobs_read_only"),
        sa.CheckConstraint(
            "status IN ('ok','running','skipped_quota','error')", name="ck_schedule_jobs_status"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_schedule_jobs_user", "schedule_jobs", ["user_id"], unique=False)
    # Partial index: the due-jobs worker scans only enabled jobs by next_run.
    op.create_index(
        "ix_schedule_jobs_due",
        "schedule_jobs",
        ["next_run"],
        unique=False,
        sqlite_where=sa.text("enabled = 1"),
        postgresql_where=sa.text("enabled"),
    )

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("market", sa.String(), nullable=False),
        sa.Column("condition", sa.JSON(), nullable=False),
        sa.Column("channels", sa.JSON(), nullable=False),
        sa.Column("quiet_start", sa.String(), nullable=True),
        sa.Column("quiet_end", sa.String(), nullable=True),
        sa.Column("daily_limit", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_triggered", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("market IN ('TW','US')", name="ck_alert_rules_market"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_rules_user", "alert_rules", ["user_id"], unique=False)
    op.create_index(
        "ix_alert_rules_scan",
        "alert_rules",
        ["market", "symbol"],
        unique=False,
        sqlite_where=sa.text("enabled = 1"),
        postgresql_where=sa.text("enabled"),
    )

    op.create_table(
        "alert_preferences",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("channels", sa.JSON(), nullable=False),
        sa.Column("enabled_categories", sa.JSON(), nullable=False),
        sa.Column("quiet_start", sa.String(), nullable=True),
        sa.Column("quiet_end", sa.String(), nullable=True),
        sa.Column("daily_limit", sa.Integer(), nullable=True),
        sa.Column("morning_digest", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "device_push_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("platform IN ('ios','android')", name="ck_device_push_tokens_platform"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "token", name="uq_device_push_tokens_user_token"),
    )
    op.create_index("ix_device_push_tokens_user", "device_push_tokens", ["user_id"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source IN ('schedule','alert','digest')", name="ck_notifications_source"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe"),
    )
    op.create_index(
        "ix_notifications_user_delivered",
        "notifications",
        ["user_id", "delivered_at"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_unread",
        "notifications",
        ["user_id"],
        unique=False,
        sqlite_where=sa.text("read_at IS NULL"),
        postgresql_where=sa.text("read_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_unread", table_name="notifications")
    op.drop_index("ix_notifications_user_delivered", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_device_push_tokens_user", table_name="device_push_tokens")
    op.drop_table("device_push_tokens")
    op.drop_table("alert_preferences")
    op.drop_index("ix_alert_rules_scan", table_name="alert_rules")
    op.drop_index("ix_alert_rules_user", table_name="alert_rules")
    op.drop_table("alert_rules")
    op.drop_index("ix_schedule_jobs_due", table_name="schedule_jobs")
    op.drop_index("ix_schedule_jobs_user", table_name="schedule_jobs")
    op.drop_table("schedule_jobs")
