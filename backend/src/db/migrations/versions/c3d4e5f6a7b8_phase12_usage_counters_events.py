"""phase12 usage counters and events

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-05 11:00:00.000000

Adds the durable usage-accounting schema behind tier-quota enforcement:

- `usage_counters` — the authoritative per-user rolling counter (composite PK
  `(user_id, counter, window_key)`) the daily-chat cap is enforced against. Mutated only via an
  atomic `INSERT … ON CONFLICT DO UPDATE SET n = n + :delta` upsert, so two concurrent turns for
  the same user in the same UTC-day window can never both slip past the cap (no read-then-write
  race). It lives in the app database, not Redis, precisely so a Redis flush can never silently
  re-grant a Free user's spent daily allowance.
- `usage_events` — the append-only per-turn rollup (token/tool/cost totals), the write-through
  target of a finished turn's accumulator snapshot. `UNIQUE (user_id, turn_id)` makes a retried
  post-turn write idempotent rather than a double-count.

Dual-dialect: text PKs, `text + CHECK` (never native enum), integer counters, matching the
earlier migrations.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_counters",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("counter", sa.String(), nullable=False),
        sa.Column("window_key", sa.String(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("n >= 0", name="ck_usage_counters_nonneg"),
        sa.PrimaryKeyConstraint("user_id", "counter", "window_key"),
    )
    op.create_index("ix_usage_counters_user", "usage_counters", ["user_id"], unique=False)

    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("turn_id", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("finmind_calls", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "turn_id", name="uq_usage_events_user_turn"),
    )
    op.create_index(
        "ix_usage_events_user_created", "usage_events", ["user_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_usage_events_user_created", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_index("ix_usage_counters_user", table_name="usage_counters")
    op.drop_table("usage_counters")
