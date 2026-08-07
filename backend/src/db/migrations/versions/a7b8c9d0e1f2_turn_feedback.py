"""turn feedback

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-07 09:00:00.000000

Adds `turn_feedback` — a minimal 👍/👎 (+ optional short comment) capture per `(turn_id,
user_id)`, replaced (upserted) on a re-submit rather than accumulating duplicate rows. This is
deliberately narrow: no rolling quality score, no alert threshold, no admin endpoints — that
pipeline remains unbuilt and is a dedicated follow-up. Dual-dialect: text UUID PK, `text + CHECK`
in place of a native enum, UTC timestamps — matching the stock-scores/theme migrations.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "turn_feedback",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("turn_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("rating", sa.String(), nullable=False),
        sa.Column("comment", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], ondelete="CASCADE"),
        sa.CheckConstraint("rating IN ('up','down')", name="ck_turn_feedback_rating"),
        sa.CheckConstraint(
            "comment IS NULL OR length(comment) <= 500", name="ck_turn_feedback_comment_len"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("turn_id", "user_id", name="uq_turn_feedback_turn_user"),
    )
    op.create_index("ix_turn_feedback_turn_id", "turn_feedback", ["turn_id"], unique=False)
    op.create_index("ix_turn_feedback_user_id", "turn_feedback", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_turn_feedback_user_id", table_name="turn_feedback")
    op.drop_index("ix_turn_feedback_turn_id", table_name="turn_feedback")
    op.drop_table("turn_feedback")
