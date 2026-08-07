"""theme change log

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-06 10:00:00.000000

Adds the `theme_change_log` audit table, deferred from the migration that shipped the theme/tier/
supply-chain schema. It records one row per create/update/replace against a theme, membership,
tier, or edge, correlated by `run_id` so an operator can see everything one seed/refresh run
touched. Like the other theme tables it carries no `user_id` — platform-wide reference data, not
per-user state. Dual-dialect: text UUID PK, `text + CHECK`, UTC timestamp.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "theme_change_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("entity", sa.String(), nullable=False),
        sa.Column("entity_ref", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entity IN ('theme','membership','tier','edge')", name="ck_theme_change_log_entity"
        ),
        sa.CheckConstraint(
            "action IN ('create','update','replace')", name="ck_theme_change_log_action"
        ),
        sa.CheckConstraint("length(detail) <= 200", name="ck_theme_change_log_detail_len"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_theme_change_log_run", "theme_change_log", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_theme_change_log_run", table_name="theme_change_log")
    op.drop_table("theme_change_log")
