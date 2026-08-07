"""themes, tiers and supply-chain edges

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-06 09:00:00.000000

Adds the platform theme/tier/supply-chain reference schema:

- `themes` — one theme per industry classification (or, later, a research/LLM concept), keyed by
  a unique natural slug (`key`); `kind`/`status`/`source` are `text + CHECK`, never native enum.
- `theme_memberships` — one (theme, symbol) row. Written as a whole snapshot per
  `(theme_id, source)` in one transaction, so the read path is predicate-free (no soft-delete).
  `UNIQUE (theme_id, symbol)` serves the screener's "symbols in theme X" hot path as an index
  range scan; the `(symbol, market)` index serves the reverse "themes for symbol X" lookup.
- `theme_tiers` — one (theme, sub_industry) upstream/midstream/downstream position. Ships empty
  in this migration; the tier-classification layer populates it.
- `supply_chain_edges` — one directed relationship between two symbols. `confidence` is a text
  band (provenance transparency, not a numeric score). Ships empty; the edge-generation layer
  populates it. Separate `from_symbol`/`to_symbol` indexes make both directions of
  "edges touching symbol X" fast.

Unlike every other table in this codebase these four carry no `user_id` — they are platform-wide
reference data, not per-user state. Dual-dialect: text UUID PKs, `text + CHECK`, JSON not JSONB,
UTC timestamps — matching the earlier migrations.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "themes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name_zh", sa.String(), nullable=False),
        sa.Column("name_en", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('industry','concept')", name="ck_themes_kind"),
        sa.CheckConstraint("status IN ('active','proposed','retired')", name="ck_themes_status"),
        sa.CheckConstraint(
            "source IN ('industry_chain','concept_research','llm_discovery')",
            name="ck_themes_source",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_themes_key"),
    )

    op.create_table(
        "theme_memberships",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("theme_id", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("market", sa.String(), nullable=False),
        sa.Column("sub_industry", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("market IN ('TW','US')", name="ck_theme_memberships_market"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_theme_memberships_confidence"
        ),
        sa.CheckConstraint(
            "source IN ('industry_chain','concept_research','llm_discovery')",
            name="ck_theme_memberships_source",
        ),
        sa.ForeignKeyConstraint(["theme_id"], ["themes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("theme_id", "symbol", name="uq_theme_memberships_theme_symbol"),
    )
    op.create_index(
        "ix_theme_memberships_symbol_market",
        "theme_memberships",
        ["symbol", "market"],
        unique=False,
    )

    op.create_table(
        "theme_tiers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("theme_id", sa.String(), nullable=False),
        sa.Column("sub_industry", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "tier IN ('upstream','midstream','downstream')", name="ck_theme_tiers_tier"
        ),
        sa.CheckConstraint("source IN ('curated','heuristic')", name="ck_theme_tiers_source"),
        sa.ForeignKeyConstraint(["theme_id"], ["themes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("theme_id", "sub_industry", name="uq_theme_tiers_theme_sub_industry"),
    )

    op.create_table(
        "supply_chain_edges",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("from_symbol", sa.String(), nullable=False),
        sa.Column("to_symbol", sa.String(), nullable=False),
        sa.Column("market", sa.String(), nullable=False),
        sa.Column("edge_type", sa.String(), nullable=False),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column("revenue_pct", sa.Float(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("from_name", sa.String(), nullable=False),
        sa.Column("to_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("market IN ('TW','US')", name="ck_supply_chain_edges_market"),
        sa.CheckConstraint(
            "edge_type IN ('supplies','customer','competes')", name="ck_supply_chain_edges_type"
        ),
        sa.CheckConstraint(
            "confidence IN ('high','medium','low')", name="ck_supply_chain_edges_confidence"
        ),
        sa.CheckConstraint(
            "source IN ('seed','llm_extraction')", name="ck_supply_chain_edges_source"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_supply_chain_edges_from", "supply_chain_edges", ["from_symbol"], unique=False
    )
    op.create_index("ix_supply_chain_edges_to", "supply_chain_edges", ["to_symbol"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_supply_chain_edges_to", table_name="supply_chain_edges")
    op.drop_index("ix_supply_chain_edges_from", table_name="supply_chain_edges")
    op.drop_table("supply_chain_edges")
    op.drop_table("theme_tiers")
    op.drop_index("ix_theme_memberships_symbol_market", table_name="theme_memberships")
    op.drop_table("theme_memberships")
    op.drop_table("themes")
