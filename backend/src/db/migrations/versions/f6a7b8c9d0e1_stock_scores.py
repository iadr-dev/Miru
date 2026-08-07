"""stock scores cache

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-06 11:00:00.000000

Adds `stock_scores` — the daily-batch-computed multi-factor composite-score cache: one row per
`(symbol, market, profile, as_of)`, replaced (upserted) once per trading day by the scoring batch
job. Backs both the per-symbol detail-page lookup (point lookup on the unique key) and the
leaderboard (sort-and-slice by `composite` off the dedicated index). Like the theme tables this
carries no `user_id` — a composite score is platform-wide reference data, not per-user state.
Dual-dialect: text UUID PK, `text + CHECK`, JSON not JSONB, UTC timestamp — matching the theme
migrations.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_scores",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("market", sa.String(), nullable=False),
        sa.Column("as_of", sa.String(), nullable=False),
        sa.Column("profile", sa.String(), nullable=False),
        sa.Column("composite", sa.Float(), nullable=True),
        sa.Column("band", sa.String(), nullable=False),
        sa.Column("momentum_percentile", sa.Float(), nullable=True),
        sa.Column("momentum_description", sa.String(), nullable=False),
        sa.Column("momentum_unavailable_reason", sa.String(), nullable=True),
        sa.Column("trend_quality_percentile", sa.Float(), nullable=True),
        sa.Column("trend_quality_description", sa.String(), nullable=False),
        sa.Column("trend_quality_unavailable_reason", sa.String(), nullable=True),
        sa.Column("value_guard_percentile", sa.Float(), nullable=True),
        sa.Column("value_guard_description", sa.String(), nullable=False),
        sa.Column("value_guard_unavailable_reason", sa.String(), nullable=True),
        sa.Column("quality_percentile", sa.Float(), nullable=True),
        sa.Column("quality_description", sa.String(), nullable=False),
        sa.Column("quality_unavailable_reason", sa.String(), nullable=True),
        sa.Column("revenue_flow_percentile", sa.Float(), nullable=True),
        sa.Column("revenue_flow_description", sa.String(), nullable=False),
        sa.Column("revenue_flow_unavailable_reason", sa.String(), nullable=True),
        sa.Column("partial_factors", sa.JSON(), nullable=False),
        sa.Column("regime_caveat", sa.String(), nullable=False),
        sa.Column("non_trading_caveat", sa.String(), nullable=False),
        sa.Column("normalization_caveat", sa.String(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("market IN ('TW','US')", name="ck_stock_scores_market"),
        sa.CheckConstraint("profile IN ('swing','position')", name="ck_stock_scores_profile"),
        sa.CheckConstraint("band IN ('偏多','中性','偏空')", name="ck_stock_scores_band"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol",
            "market",
            "profile",
            "as_of",
            name="uq_stock_scores_symbol_market_profile_as_of",
        ),
    )
    op.create_index(
        "ix_stock_scores_leaderboard",
        "stock_scores",
        ["market", "profile", "as_of", "composite"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_stock_scores_leaderboard", table_name="stock_scores")
    op.drop_table("stock_scores")
