"""Supply-chain relations, their evidence, and the derived chain positions.

An index over the operator's private files (the files are the source of truth, rebuilt into
these tables by `analytics.supply_chain.jobs`). Platform reference data, so — like the theme
tables — there is **no `user_id`** column. Constrained text uses CHECKs, never native ENUMs.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.models._common import UTC_TIMESTAMP as _UTC


class ScRelationRow(Base):
    __tablename__ = "sc_relations"

    id: Mapped[str] = mapped_column(primary_key=True)
    supplier_symbol: Mapped[str]
    supplier_market: Mapped[str]
    customer_symbol: Mapped[str]
    customer_market: Mapped[str]
    kind: Mapped[str]
    product: Mapped[str | None] = mapped_column(default=None)
    # `product` or '' — a NOT NULL column so the unique key behaves the same on SQLite/Postgres.
    product_key: Mapped[str] = mapped_column(default="")
    status: Mapped[str]
    grade: Mapped[str]
    revenue_pct: Mapped[float | None] = mapped_column(default=None)
    as_of: Mapped[date | None] = mapped_column(default=None)
    last_verified_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    origin: Mapped[str]
    supplier_name: Mapped[str] = mapped_column(default="")
    customer_name: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint("supplier_market IN ('TW','US')", name="ck_sc_relations_supplier_market"),
        CheckConstraint("customer_market IN ('TW','US')", name="ck_sc_relations_customer_market"),
        CheckConstraint("kind IN ('supplies','competes')", name="ck_sc_relations_kind"),
        CheckConstraint(
            "status IN ('candidate','verified','stale','rejected')", name="ck_sc_relations_status"
        ),
        CheckConstraint("grade IN ('high','medium','low')", name="ck_sc_relations_grade"),
        CheckConstraint("origin IN ('research','seeded','manual')", name="ck_sc_relations_origin"),
        CheckConstraint(
            "revenue_pct IS NULL OR (revenue_pct >= 0 AND revenue_pct <= 100)",
            name="ck_sc_relations_revenue_pct",
        ),
        UniqueConstraint(
            "supplier_symbol",
            "supplier_market",
            "customer_symbol",
            "customer_market",
            "kind",
            "product_key",
            name="uq_sc_relations_natural_key",
        ),
        Index("ix_sc_relations_supplier", "supplier_symbol", "supplier_market"),
        Index("ix_sc_relations_customer", "customer_symbol", "customer_market"),
    )


class ScEvidenceRow(Base):
    __tablename__ = "sc_evidence"

    id: Mapped[str] = mapped_column(primary_key=True)
    relation_id: Mapped[str] = mapped_column(ForeignKey("sc_relations.id", ondelete="CASCADE"))
    url: Mapped[str]
    publisher: Mapped[str]
    trust: Mapped[str]
    published_on: Mapped[date | None] = mapped_column(default=None)
    excerpt: Mapped[str] = mapped_column(default="")
    retrieved_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    checker: Mapped[str]
    supports: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        CheckConstraint(
            "trust IN ('primary','secondary','tertiary','rejected')", name="ck_sc_evidence_trust"
        ),
        CheckConstraint("checker IN ('rule','llm','human')", name="ck_sc_evidence_checker"),
        CheckConstraint("length(excerpt) <= 300", name="ck_sc_evidence_excerpt_len"),
        Index("ix_sc_evidence_relation", "relation_id"),
    )


class ScPositionRow(Base):
    """One company's computed position in one industry chain. Derived data: the positions job
    rewrites the whole table, so nothing here is hand-edited (operator decisions live in the
    private overrides file). Platform reference data, so no `user_id`."""

    __tablename__ = "sc_positions"

    id: Mapped[str] = mapped_column(primary_key=True)
    symbol: Mapped[str]
    market: Mapped[str]
    chain_key: Mapped[str]
    chain_name: Mapped[str] = mapped_column(default="")
    position: Mapped[str]
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    method: Mapped[str]
    confidence: Mapped[float]
    reasons: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    inputs_hash: Mapped[str] = mapped_column(default="")
    computed_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint("market IN ('TW','US')", name="ck_sc_positions_market"),
        CheckConstraint(
            "position IN ('upstream','midstream','downstream')", name="ck_sc_positions_position"
        ),
        CheckConstraint("method IN ('override','evidence','prior')", name="ck_sc_positions_method"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_sc_positions_confidence"),
        UniqueConstraint("symbol", "market", "chain_key", name="uq_sc_positions_natural_key"),
        Index("ix_sc_positions_symbol", "symbol", "market"),
        Index("ix_sc_positions_chain", "chain_key"),
        # Exactly one primary chain per company, enforced by the database.
        Index(
            "uq_sc_positions_primary",
            "symbol",
            "market",
            unique=True,
            sqlite_where=text("is_primary = 1"),
            postgresql_where=text("is_primary"),
        ),
    )


class ScConceptThemeRow(Base):
    """One concept theme (a cross-industry cut such as a technology wave), loaded from the
    operator's theme files. `related` is a JSON list of display names. Platform reference data,
    so no `user_id`. Each theme is also mirrored into `themes`/`theme_memberships`
    (`kind='concept'`) so the generic theme tools and the screener chips see it."""

    __tablename__ = "sc_concept_themes"

    id: Mapped[str] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True)
    name_zh: Mapped[str]
    name_en: Mapped[str] = mapped_column(default="")
    description: Mapped[str] = mapped_column(default="")
    related: Mapped[list[str]] = mapped_column(JSON, default=list)


class ScConceptMemberRow(Base):
    __tablename__ = "sc_concept_members"

    id: Mapped[str] = mapped_column(primary_key=True)
    theme_key: Mapped[str] = mapped_column(ForeignKey("sc_concept_themes.key", ondelete="CASCADE"))
    symbol: Mapped[str]
    market: Mapped[str]
    name: Mapped[str] = mapped_column(default="")
    position: Mapped[str]
    origin: Mapped[str]

    __table_args__ = (
        CheckConstraint("market IN ('TW','US')", name="ck_sc_concept_members_market"),
        CheckConstraint(
            "position IN ('upstream','midstream','downstream','related')",
            name="ck_sc_concept_members_position",
        ),
        CheckConstraint(
            "origin IN ('seeded','curated','proposed')", name="ck_sc_concept_members_origin"
        ),
        UniqueConstraint("theme_key", "symbol", "market", name="uq_sc_concept_members_natural"),
        Index("ix_sc_concept_members_symbol", "symbol", "market"),
    )


class ScEntityRow(Base):
    """A named thing the operator's reports mention (a company, technology, material, ...).
    `id` is the entity's own name string; `symbol`/`market` are set for listed companies."""

    __tablename__ = "sc_entities"

    id: Mapped[str] = mapped_column(primary_key=True)
    type: Mapped[str]
    symbol: Mapped[str | None] = mapped_column(default=None)
    market: Mapped[str | None] = mapped_column(default=None)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    mentions: Mapped[int] = mapped_column(default=0)
    reports: Mapped[int] = mapped_column(default=0)

    __table_args__ = (
        CheckConstraint(
            "type IN ('tw_company','intl_company','technology','material','application','product')",
            name="ck_sc_entities_type",
        ),
        CheckConstraint("market IS NULL OR market IN ('TW','US')", name="ck_sc_entities_market"),
        CheckConstraint("mentions >= 0 AND reports >= 0", name="ck_sc_entities_counts"),
        Index("ix_sc_entities_symbol", "symbol", "market"),
    )


class ScEntityMentionRow(Base):
    """How often one company's report mentions one entity in one section. `role_key` is the role
    or '' (NOT NULL so the unique key behaves the same on SQLite and Postgres)."""

    __tablename__ = "sc_entity_mentions"

    id: Mapped[str] = mapped_column(primary_key=True)
    symbol: Mapped[str]
    entity_id: Mapped[str] = mapped_column(ForeignKey("sc_entities.id", ondelete="CASCADE"))
    section: Mapped[str]
    role: Mapped[str | None] = mapped_column(default=None)
    role_key: Mapped[str] = mapped_column(default="")
    count: Mapped[int] = mapped_column(default=1)

    __table_args__ = (
        CheckConstraint(
            "section IN ('business','chain','counterparty')", name="ck_sc_entity_mentions_section"
        ),
        CheckConstraint(
            "role IS NULL OR role IN ('customer','supplier')", name="ck_sc_entity_mentions_role"
        ),
        CheckConstraint("count >= 0", name="ck_sc_entity_mentions_count"),
        UniqueConstraint(
            "symbol", "entity_id", "section", "role_key", name="uq_sc_entity_mentions_natural"
        ),
        Index("ix_sc_entity_mentions_entity", "entity_id"),
        Index("ix_sc_entity_mentions_symbol", "symbol"),
    )
