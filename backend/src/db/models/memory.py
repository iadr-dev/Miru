"""Per-user memory, watchlists and saved screener screens."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from db.models._common import CASCADE_DELETE_ORPHAN as _CASCADE_DELETE_ORPHAN
from db.models._common import MARKET_TW_US_CHECK as _MARKET_TW_US_CHECK
from db.models._common import UTC_TIMESTAMP as _UTC


class MemoryRow(Base):
    """`type='watchlist'` rows are legacy-only per the ADR that made watchlists a first-class
    relational store — the CHECK value is kept for backward compatibility with rows written
    before that store existed, but nothing in this codebase writes a new one; `memory/store.py`
    enforces that at the write path, not here."""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(index=True)
    scope: Mapped[str]
    thread_id: Mapped[str | None] = mapped_column(default=None)
    type: Mapped[str]
    key: Mapped[str]
    content: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    source_ref: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)
    # When recall last put this memory in front of the model, and how often (the settings
    # page shows both). server_default so the additive upgrade can add the columns.
    last_used_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    use_count: Mapped[int] = mapped_column(default=0, server_default="0")

    __table_args__ = (
        CheckConstraint("scope IN ('user','thread')", name="ck_memories_scope"),
        CheckConstraint(
            "type IN ('watchlist','preference','finding','entity_note')", name="ck_memories_type"
        ),
        UniqueConstraint("user_id", "scope", "type", "key", name="uq_memories_upsert_target"),
        Index("ix_memories_user_thread", "user_id", "thread_id"),
    )


class WatchlistRow(Base):
    __tablename__ = "watchlists"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    market: Mapped[str]
    asset_class: Mapped[str]
    sort_order: Mapped[str]
    is_default: Mapped[bool] = mapped_column(default=False)
    # Optional label colour: a palette key (`WATCHLIST_COLORS`), None = no colour.
    color: Mapped[str | None] = mapped_column(default=None)
    version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)
    deleted_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)

    items: Mapped[list[WatchlistItemRow]] = relationship(
        back_populates="watchlist", cascade=_CASCADE_DELETE_ORPHAN
    )

    __table_args__ = (
        CheckConstraint(_MARKET_TW_US_CHECK, name="ck_watchlists_market"),
        CheckConstraint("asset_class IN ('stock','etf')", name="ck_watchlists_asset_class"),
        Index(
            "ix_watchlists_user_market_class_sort",
            "user_id",
            "market",
            "asset_class",
            "sort_order",
        ),
        # One non-deleted group name per (user, market, asset_class) — partial-unique index.
        Index(
            "uq_watchlists_user_market_class_name",
            "user_id",
            "market",
            "asset_class",
            "name",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class WatchlistItemRow(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[str] = mapped_column(primary_key=True)
    watchlist_id: Mapped[str] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(index=True)
    symbol: Mapped[str]
    market: Mapped[str]
    asset_class: Mapped[str]
    note: Mapped[str | None] = mapped_column(default=None)
    sort_order: Mapped[str]
    added_at: Mapped[datetime] = mapped_column(_UTC)

    watchlist: Mapped[WatchlistRow] = relationship(back_populates="items")

    __table_args__ = (
        CheckConstraint(_MARKET_TW_US_CHECK, name="ck_watchlist_items_market"),
        CheckConstraint("asset_class IN ('stock','etf')", name="ck_watchlist_items_asset_class"),
        CheckConstraint("note IS NULL OR length(note) <= 280", name="ck_watchlist_items_note_len"),
        UniqueConstraint("watchlist_id", "symbol", name="uq_watchlist_items_watchlist_symbol"),
        Index("ix_watchlist_items_watchlist_sort", "watchlist_id", "sort_order"),
        Index("ix_watchlist_items_user_symbol", "user_id", "symbol"),
    )


class ScreenerScreenRow(Base):
    """A user's saved screener condition set (README.md §6 `SavedScreens`) — `market` is the
    screener's own three-tab model (`'TW'|'US'|'ETF'`, not `schemas.enums.Market`, which has no
    `ETF` value), so this table's check constraint is its own rather than reusing
    `_MARKET_TW_US_CHECK`. `conditions_json`/`gate_overrides_json` are opaque serialized request
    bodies (the same shape `POST /analytics/screener/run` accepts) — this store never
    interprets them, only persists/replays, so it needs no schema change when the condition/gate
    shape evolves. No `version`/lexorank: a saved-screen list has no user-visible drag-reorder in
    this pass's spec (README.md §6 lists load/rename/delete only), so the optimistic-CAS/lexorank
    machinery `WatchlistRow` needs for its drag-and-drop rail would be unused complexity here."""

    __tablename__ = "screener_screens"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    market: Mapped[str]
    conditions_json: Mapped[str]  # JSON array — see class docstring
    match: Mapped[str] = mapped_column(default="AND")
    gate_overrides_json: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint("market IN ('TW','US','ETF')", name="ck_screener_screens_market"),
        CheckConstraint("match IN ('AND','OR')", name="ck_screener_screens_match"),
        # One screen name per (user, market) — same partial-unique shape as
        # `uq_watchlists_user_market_class_name`, minus `asset_class` (this table has none).
        Index(
            "uq_screener_screens_user_market_name",
            "user_id",
            "market",
            "name",
            unique=True,
        ),
        Index("ix_screener_screens_user_market", "user_id", "market"),
    )
