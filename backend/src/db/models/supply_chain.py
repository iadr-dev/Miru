"""Supply-chain relations and their evidence.

An index over the operator's private files (the files are the source of truth, rebuilt into
these tables by `analytics.supply_chain.jobs`). Platform reference data, so — like the theme
tables — there is **no `user_id`** column. Constrained text uses CHECKs, never native ENUMs.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, UniqueConstraint
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
