"""Platform-wide theme, tier and supply-chain reference data."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.models._common import MARKET_TW_US_CHECK as _MARKET_TW_US_CHECK
from db.models._common import UTC_TIMESTAMP as _UTC


class ThemeRow(Base):
    """One theme — an industry classification (`kind='industry'`, e.g. 半導體) or, in a later
    layer, a research/LLM-discovered concept (`kind='concept'`, e.g. `cowos-advanced-packaging`).

    Unlike every other table in this file, these four theme/tier/edge tables carry **no
    `user_id`**: they are platform-wide reference data (a stock's industry membership is the same
    for everyone), not per-user state, so the `WHERE user_id = ?` isolation wrapper does not apply
    to them. `key` is the natural slug a caller looks a theme up by — for industry themes it is
    the industry name itself, which is already unique within the classification roster.
    """

    __tablename__ = "themes"

    id: Mapped[str] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True)
    name_zh: Mapped[str]
    name_en: Mapped[str] = mapped_column(default="")
    kind: Mapped[str]
    status: Mapped[str] = mapped_column(default="active")
    source: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint("kind IN ('industry','concept')", name="ck_themes_kind"),
        CheckConstraint("status IN ('active','proposed','retired')", name="ck_themes_status"),
        CheckConstraint(
            "source IN ('industry_chain','concept_research','llm_discovery')",
            name="ck_themes_source",
        ),
    )


class ThemeMembershipRow(Base):
    """One (theme, symbol) membership. Written as a whole snapshot per `(theme_id, source)`
    inside one transaction — a refresh replaces that source's rows for the theme rather than
    merging row-by-row, so the read path never needs a soft-delete predicate (there is no
    `deleted_at`): every row present is current. Mirrors the ingest store's replace-by-partition
    philosophy on the relational side."""

    __tablename__ = "theme_memberships"

    id: Mapped[str] = mapped_column(primary_key=True)
    theme_id: Mapped[str] = mapped_column(ForeignKey("themes.id", ondelete="CASCADE"))
    symbol: Mapped[str]
    market: Mapped[str]
    sub_industry: Mapped[str] = mapped_column(default="")
    confidence: Mapped[float] = mapped_column(default=1.0)
    source: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(_MARKET_TW_US_CHECK, name="ck_theme_memberships_market"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_theme_memberships_confidence"
        ),
        CheckConstraint(
            "source IN ('industry_chain','concept_research','llm_discovery')",
            name="ck_theme_memberships_source",
        ),
        # Serves the screener's hot path ("which symbols are in theme X") as an index range scan.
        UniqueConstraint("theme_id", "symbol", name="uq_theme_memberships_theme_symbol"),
        # Serves the reverse lookup ("which themes does symbol X belong to").
        Index("ix_theme_memberships_symbol_market", "symbol", "market"),
    )


class ThemeTierRow(Base):
    """One (theme, sub-industry) upstream/midstream/downstream position. Not populated by the
    Layer-1 industry-chain seed — the tier-classification layer fills it — but the table ships in
    the same migration so the theme schema lands as one coherent unit."""

    __tablename__ = "theme_tiers"

    id: Mapped[str] = mapped_column(primary_key=True)
    theme_id: Mapped[str] = mapped_column(ForeignKey("themes.id", ondelete="CASCADE"))
    sub_industry: Mapped[str]
    tier: Mapped[str]
    source: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(
            "tier IN ('upstream','midstream','downstream')", name="ck_theme_tiers_tier"
        ),
        CheckConstraint("source IN ('curated','heuristic')", name="ck_theme_tiers_source"),
        UniqueConstraint("theme_id", "sub_industry", name="uq_theme_tiers_theme_sub_industry"),
    )


class SupplyChainEdgeRow(Base):
    """One directed supply-chain relationship between two symbols. `confidence` is a text band
    (`high`/`medium`/`low`) rather than a number because it records provenance transparency — how
    trustworthy the extraction is — not a computed score. Not populated by this layer (the edge-
    generation layer fills it); ships now for schema coherence."""

    __tablename__ = "supply_chain_edges"

    id: Mapped[str] = mapped_column(primary_key=True)
    from_symbol: Mapped[str]
    to_symbol: Mapped[str]
    market: Mapped[str]
    edge_type: Mapped[str]
    confidence: Mapped[str]
    revenue_pct: Mapped[float | None] = mapped_column(default=None)
    source: Mapped[str]
    from_name: Mapped[str] = mapped_column(default="")
    to_name: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(_MARKET_TW_US_CHECK, name="ck_supply_chain_edges_market"),
        CheckConstraint(
            "edge_type IN ('supplies','customer','competes')", name="ck_supply_chain_edges_type"
        ),
        CheckConstraint(
            "confidence IN ('high','medium','low')", name="ck_supply_chain_edges_confidence"
        ),
        CheckConstraint("source IN ('seed','llm_extraction')", name="ck_supply_chain_edges_source"),
        Index("ix_supply_chain_edges_from", "from_symbol"),
        Index("ix_supply_chain_edges_to", "to_symbol"),
    )


class ThemeChangeLogRow(Base):
    """An append-only audit trail of theme/membership/tier/edge writes. `run_id` correlates every
    row a single seed/refresh invocation produced, so an operator can see exactly what one run
    changed. `detail` is capped short on purpose — it summarizes a write (member count, confidence
    distribution), it is not a place to dump payloads. Like the other theme tables it carries no
    `user_id`: this is platform-wide reference data, not per-user state."""

    __tablename__ = "theme_change_log"

    id: Mapped[str] = mapped_column(primary_key=True)
    entity: Mapped[str]
    entity_ref: Mapped[str]
    action: Mapped[str]
    source: Mapped[str]
    detail: Mapped[str] = mapped_column(default="")
    run_id: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(
            "entity IN ('theme','membership','tier','edge')", name="ck_theme_change_log_entity"
        ),
        CheckConstraint(
            "action IN ('create','update','replace')", name="ck_theme_change_log_action"
        ),
        CheckConstraint("length(detail) <= 200", name="ck_theme_change_log_detail_len"),
        Index("ix_theme_change_log_run", "run_id"),
    )
