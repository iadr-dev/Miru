"""SQLAlchemy ORM models for the first tables this codebase persists: conversations
(threads/turns/messages), the SSE replay log (`stream_events`), long-term memory (`memories`),
and the first-class watchlist store (`watchlists`/`watchlist_items`).

Every table follows the project's dual-dialect conventions (see `db/base.py`): text UUID PKs
(app-generated, never a DB-native `gen_random_uuid()`), UTC timestamps, `text + CHECK` in place
of native enums, JSON (not JSONB) for portable structured columns, and `ON DELETE CASCADE` from
`users`/`threads` (SQLite needs `PRAGMA foreign_keys=ON`, wired in `db/sqlite_pragma.py`).

The `users` table now exists (Phase 9, accounts/entitlements). The deferred-FK decision is
resolved as follows: the **new** account/pet tables added this phase (`oauth_accounts`,
`user_secrets`, `sessions`, `user_pets`, `user_pet_stats`) carry a real
`ForeignKey(_USERS_ID_FK, ondelete="CASCADE")` — they are pure children of a user with no
independent lifecycle. The **pre-existing** per-row `user_id` columns on `threads`/`turns`/
`messages`/`stream_events`/`memories`/`watchlists`/`watchlist_items` are deliberately left as
bare indexed columns and are *not* retrofitted with a FK: they are denormalized
isolation-scoping keys (the `WHERE user_id = ?` store wrapper + Postgres RLS is the isolation
mechanism, per `backend/CLAUDE.md` §6, not a DB-level FK), and retrofitting a hard FK onto a
denormalized copy would both fight that design and force every conversation/memory row to
already have a `users` parent — a coupling those stores intentionally don't take. Identity is
the `users` table; these columns point at it by convention, scoped in SQL.

`Base.metadata.create_all()` (called once at process startup, see `db/base.py`) is this project's
sole schema-provisioning mechanism — no Alembic migration chain. Every partial (`WHERE`-qualified)
index/constraint below is expressed directly via `Index(..., sqlite_where=..., postgresql_where=
...)`, which SQLAlchemy Core supports natively on both dialects, so `create_all()` alone produces
a complete, dual-dialect-correct schema with no DDL that only a migration could express.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base

_UTC = DateTime(timezone=True)

_CASCADE_DELETE_ORPHAN = "all, delete-orphan"
_TURNS_ID_FK = "turns.id"
_USERS_ID_FK = "users.id"
_MARKET_TW_US_CHECK = "market IN ('TW','US')"

# ---- Conversations -------------------------------------------------------------------------


class ThreadRow(Base):
    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(default="")
    starred: Mapped[bool] = mapped_column(default=False)
    archived: Mapped[bool] = mapped_column(default=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)  # JSON array — portability
    model_override: Mapped[str | None] = mapped_column(default=None)
    personality_override: Mapped[str | None] = mapped_column(default=None)
    active_leaf_message_id: Mapped[str | None] = mapped_column(default=None)
    draft_content: Mapped[str | None] = mapped_column(default=None)
    last_message_at: Mapped[datetime] = mapped_column(_UTC)
    version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)
    deleted_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)

    turns: Mapped[list[TurnRow]] = relationship(
        back_populates="thread", cascade=_CASCADE_DELETE_ORPHAN
    )

    __table_args__ = (
        Index("ix_threads_user_last_message", "user_id", "last_message_at"),
        Index("ix_threads_user_created", "user_id", "created_at"),
    )


class TurnRow(Base):
    __tablename__ = "turns"

    id: Mapped[str] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(index=True)  # denormalized for isolation queries
    role: Mapped[str]
    status: Mapped[str] = mapped_column(default="running")
    parent_turn_id: Mapped[str | None] = mapped_column(default=None)
    regenerate_of: Mapped[str | None] = mapped_column(default=None)
    framework: Mapped[str | None] = mapped_column(default=None)
    idempotency_key: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(_UTC)

    thread: Mapped[ThreadRow] = relationship(back_populates="turns")
    messages: Mapped[list[MessageRow]] = relationship(
        back_populates="turn", cascade=_CASCADE_DELETE_ORPHAN
    )

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="ck_turns_role"),
        CheckConstraint("status IN ('running','done','cancelled','error')", name="ck_turns_status"),
        CheckConstraint(
            "framework IS NULL OR framework IN ('none','single','subagent','team')",
            name="ck_turns_framework",
        ),
        Index("ix_turns_thread_created", "thread_id", "created_at"),
        Index("ix_turns_user_created", "user_id", "created_at"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_turns_user_idempotency_key"),
        # At most one 'running' turn per thread — a partial-unique index, expressed with the
        # dialect-specific `postgresql_where`/`sqlite_where` kwargs `Index(...)` accepts directly
        # (no separate migration DDL needed for this).
        Index(
            "uq_turns_one_running_per_thread",
            "thread_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
    )


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(primary_key=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey(_TURNS_ID_FK, ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(index=True)  # denormalized for isolation + FTS
    parent_id: Mapped[str | None] = mapped_column(default=None)
    seq: Mapped[int]
    type: Mapped[str]
    content: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(_UTC)

    turn: Mapped[TurnRow] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint(
            "type IN ('text','thinking','tool_use','tool_result','caveat','rich_card',"
            "'steering','branch_summary','compaction','attachment','web_search_sources')",
            name="ck_messages_type",
        ),
        Index("ix_messages_turn_seq", "turn_id", "seq"),
        Index("ix_messages_parent", "parent_id"),
        Index("ix_messages_user", "user_id"),
    )


class StreamEventRow(Base):
    """SSE reconnect-replay log — see `events/bus.py`'s docstring for the live-fanout vs
    durable-replay split this table is the durable half of."""

    __tablename__ = "stream_events"

    id: Mapped[str] = mapped_column(primary_key=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey(_TURNS_ID_FK, ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(index=True)
    event_seq: Mapped[int]
    event_type: Mapped[str]
    agent_id: Mapped[str | None] = mapped_column(default=None)
    parent_agent_id: Mapped[str | None] = mapped_column(default=None)
    node_id: Mapped[str | None] = mapped_column(default=None)
    data: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        UniqueConstraint("turn_id", "event_seq", name="uq_stream_events_turn_seq"),
        Index("ix_stream_events_user_created", "user_id", "created_at"),
    )


# ---- Long-term memory -----------------------------------------------------------------------


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

    __table_args__ = (
        CheckConstraint("scope IN ('user','thread')", name="ck_memories_scope"),
        CheckConstraint(
            "type IN ('watchlist','preference','finding','entity_note')", name="ck_memories_type"
        ),
        UniqueConstraint("user_id", "scope", "type", "key", name="uq_memories_upsert_target"),
        Index("ix_memories_user_thread", "user_id", "thread_id"),
    )


# ---- Watchlists (first-class relational store) ----------------------------------------------


class WatchlistRow(Base):
    __tablename__ = "watchlists"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    market: Mapped[str]
    asset_class: Mapped[str]
    sort_order: Mapped[str]
    is_default: Mapped[bool] = mapped_column(default=False)
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


# ---- Accounts, identity, entitlements (Phase 9) --------------------------------------------


class UserRow(Base):
    """The identity + entitlement anchor. `tier` is a derived cache (written by the billing
    webhook in a later phase from the active subscription; defaults to `free` until then).
    `personality_id` is the user's selected agent-voice preset (defaults to `steady`).

    `email` is nullable (a LINE user may lack one) and identity is keyed on `oauth_accounts`,
    not email — see `uq_users_email` below for the partial-unique-where-not-null."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(default=None)
    display_name: Mapped[str] = mapped_column(default="")
    picture_url: Mapped[str | None] = mapped_column(default=None)
    birthday: Mapped[str | None] = mapped_column(default=None)  # ISO date string — portable
    phone_country_code: Mapped[str | None] = mapped_column(default=None)
    phone_number: Mapped[str | None] = mapped_column(default=None)
    nationality: Mapped[str | None] = mapped_column(default=None)
    tier: Mapped[str] = mapped_column(default="free")
    personality_id: Mapped[str] = mapped_column(default="steady")
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)
    deleted_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)

    oauth_accounts: Mapped[list[OAuthAccountRow]] = relationship(
        back_populates="user", cascade=_CASCADE_DELETE_ORPHAN
    )
    secrets: Mapped[list[UserSecretRow]] = relationship(
        back_populates="user", cascade=_CASCADE_DELETE_ORPHAN
    )
    sessions: Mapped[list[SessionRow]] = relationship(
        back_populates="user", cascade=_CASCADE_DELETE_ORPHAN
    )

    __table_args__ = (
        CheckConstraint("tier IN ('free','premium','pro')", name="ck_users_tier"),
        # At most one non-null email across users (a LINE user may have none) — partial-unique.
        Index(
            "uq_users_email",
            "email",
            unique=True,
            sqlite_where=text("email IS NOT NULL"),
            postgresql_where=text("email IS NOT NULL"),
        ),
    )


class OAuthAccountRow(Base):
    """One linked provider identity for a user — many rows per user (Google + LINE = one
    account). `(provider, provider_account_id)` is globally unique: one provider identity maps
    to exactly one user."""

    __tablename__ = "oauth_accounts"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    provider: Mapped[str]
    provider_account_id: Mapped[str]
    email_at_provider: Mapped[str | None] = mapped_column(default=None)
    linked_at: Mapped[datetime] = mapped_column(_UTC)

    user: Mapped[UserRow] = relationship(back_populates="oauth_accounts")

    __table_args__ = (
        CheckConstraint("provider IN ('google','line')", name="ck_oauth_accounts_provider"),
        UniqueConstraint(
            "provider", "provider_account_id", name="uq_oauth_accounts_provider_identity"
        ),
    )


class UserSecretRow(Base):
    """Encrypted-at-rest secret store: BYOK LLM keys, FinMind tokens, channel tokens.
    `ciphertext` is the envelope-encrypted secret bytes — never logged, never returned in
    plaintext (see `accounts.secrets`). One secret per `(user_id, kind, provider)`."""

    __tablename__ = "user_secrets"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    kind: Mapped[str]
    provider: Mapped[str]
    ciphertext: Mapped[bytes]
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    user: Mapped[UserRow] = relationship(back_populates="secrets")

    __table_args__ = (
        CheckConstraint(
            "kind IN ('llm_key','finmind_token','channel_token','asr_key','tts_key')",
            name="ck_user_secrets_kind",
        ),
        UniqueConstraint("user_id", "kind", "provider", name="uq_user_secrets_target"),
    )


class SessionRow(Base):
    """Refresh-token store — rotation lineage + reuse detection. Stores only a hash of the
    refresh token, never the raw token. `family_id` groups a rotation lineage: presenting a
    rotated-out token (reuse) revokes the whole family."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    family_id: Mapped[str] = mapped_column(index=True)
    rotated_from: Mapped[str | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    user_agent: Mapped[str | None] = mapped_column(default=None)
    ip: Mapped[str | None] = mapped_column(default=None)
    expires_at: Mapped[datetime] = mapped_column(_UTC)
    created_at: Mapped[datetime] = mapped_column(_UTC)

    user: Mapped[UserRow] = relationship(back_populates="sessions")

    __table_args__ = (Index("ix_sessions_expires_at", "expires_at"),)


# ---- Pets & gamification (Phase 9, cosmetic-only) ------------------------------------------


class UserPetRow(Base):
    """One owned pet. Cosmetic only — no tier/entitlement/data linkage. `pet_id` is a catalog
    key (definitions live in `pets` config code, not a table). At most one `is_active=True` per
    user (enforced in the equip transaction + `uq_user_pets_one_active_per_user` below)."""

    __tablename__ = "user_pets"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    pet_id: Mapped[str]
    is_active: Mapped[bool] = mapped_column(default=False)
    level: Mapped[int] = mapped_column(default=1)
    xp: Mapped[int] = mapped_column(default=0)
    obtained_via: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint("obtained_via IN ('pull','admin')", name="ck_user_pets_obtained_via"),
        Index("ix_user_pets_user_active", "user_id", "is_active"),
        # At most one equipped pet per user — partial-unique index. SQLite stores booleans as
        # 0/1 so its predicate must compare against `1`; Postgres accepts the bare boolean column
        # as a predicate directly.
        Index(
            "uq_user_pets_one_active_per_user",
            "user_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active"),
        ),
    )


class UserPetStatsRow(Base):
    """One row per user — gacha/engagement counters. `available_pulls` defaults to 1 (the free
    register pull). All mutations are atomic upserts / reserve-before-act (cosmetic, but the
    same concurrency discipline as everything else)."""

    __tablename__ = "user_pet_stats"

    user_id: Mapped[str] = mapped_column(
        ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), primary_key=True
    )
    total_pulls: Mapped[int] = mapped_column(default=0)
    streak_days: Mapped[int] = mapped_column(default=0)
    last_login_date: Mapped[str | None] = mapped_column(default=None)  # ISO date string
    chat_count: Mapped[int] = mapped_column(default=0)
    available_pulls: Mapped[int] = mapped_column(default=1)
    pity_counter: Mapped[int] = mapped_column(default=0)


# ---- Scheduler & alerts (Phase 10) ---------------------------------------------------------


class ScheduleJobRow(Base):
    """A user-authorized scheduled job. `read_only` is enforced `True` at the store write path
    (the scheduling carve-out forbids streaming/high-frequency polling); it exists as a column
    so the invariant is visible in the row, not just in code. `status='running'` is what lets a
    restarted worker detect a run orphaned by a crash."""

    __tablename__ = "schedule_jobs"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    trigger_type: Mapped[str]
    trigger_spec: Mapped[str]
    trigger_market: Mapped[str] = mapped_column(default="TW")
    skill_ref: Mapped[str]
    skill_inputs: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    read_only: Mapped[bool] = mapped_column(default=True)
    delivery_channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    quiet_start: Mapped[str | None] = mapped_column(default=None)
    quiet_end: Mapped[str | None] = mapped_column(default=None)
    daily_limit: Mapped[int | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_run: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    next_run: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    status: Mapped[str] = mapped_column(default="ok")
    version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('cron','market_event')", name="ck_schedule_jobs_trigger_type"
        ),
        CheckConstraint("trigger_market IN ('TW','US')", name="ck_schedule_jobs_market"),
        # `= true` (not `= 1`) so the boolean compares to a boolean on Postgres, which rejects
        # `boolean = integer`; SQLite accepts the `true` literal against its integer-stored boolean.
        CheckConstraint("read_only = true", name="ck_schedule_jobs_read_only"),
        CheckConstraint(
            "status IN ('ok','running','skipped_quota','error')", name="ck_schedule_jobs_status"
        ),
        Index("ix_schedule_jobs_user", "user_id"),
        # The due-jobs worker scans only enabled jobs by next_run — a partial index.
        Index(
            "ix_schedule_jobs_due",
            "next_run",
            sqlite_where=text("enabled = 1"),
            postgresql_where=text("enabled"),
        ),
    )


class AlertRuleRow(Base):
    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    symbol: Mapped[str]
    market: Mapped[str]
    condition: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    quiet_start: Mapped[str | None] = mapped_column(default=None)
    quiet_end: Mapped[str | None] = mapped_column(default=None)
    daily_limit: Mapped[int | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_triggered: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(_MARKET_TW_US_CHECK, name="ck_alert_rules_market"),
        Index("ix_alert_rules_user", "user_id"),
        # The alert-engine scan — a partial index over only enabled rules.
        Index(
            "ix_alert_rules_scan",
            "market",
            "symbol",
            sqlite_where=text("enabled = 1"),
            postgresql_where=text("enabled"),
        ),
    )


class AlertPreferenceRow(Base):
    __tablename__ = "alert_preferences"

    user_id: Mapped[str] = mapped_column(
        ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), primary_key=True
    )
    channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    quiet_start: Mapped[str | None] = mapped_column(default=None)
    quiet_end: Mapped[str | None] = mapped_column(default=None)
    daily_limit: Mapped[int | None] = mapped_column(default=None)
    morning_digest: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(_UTC)


class DevicePushTokenRow(Base):
    __tablename__ = "device_push_tokens"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    platform: Mapped[str]
    token: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(_UTC)
    last_seen: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint("platform IN ('ios','android')", name="ck_device_push_tokens_platform"),
        UniqueConstraint("user_id", "token", name="uq_device_push_tokens_user_token"),
        Index("ix_device_push_tokens_user", "user_id"),
    )


class NotificationRow(Base):
    """A delivered/persisted notification. The `(user_id, dedupe_key)` unique constraint is the
    idempotent-delivery guard: at-least-once execution can attempt the same
    `(source_id, trigger_date, channel)` twice, but the second insert collides and is a no-op."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    source: Mapped[str]
    source_id: Mapped[str]
    channel: Mapped[str]
    dedupe_key: Mapped[str]
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    delivered_at: Mapped[datetime] = mapped_column(_UTC)
    read_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)

    __table_args__ = (
        CheckConstraint("source IN ('schedule','alert','digest')", name="ck_notifications_source"),
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe"),
        Index("ix_notifications_user_delivered", "user_id", "delivered_at"),
        # The unread-badge scan — a partial index over only unread rows.
        Index(
            "ix_notifications_unread",
            "user_id",
            sqlite_where=text("read_at IS NULL"),
            postgresql_where=text("read_at IS NULL"),
        ),
    )


# ---- Durable usage accounting (Phase 12) ---------------------------------------------------


class UsageCounterRow(Base):
    """The authoritative per-user rolling counter behind daily-chat-limit enforcement.

    One row per `(user_id, counter, window_key)` — e.g. `('chat', '2026-08-05')` for the
    UTC-day chat count. The count is mutated only via an atomic
    `INSERT … ON CONFLICT DO UPDATE SET n = n + :delta` upsert, never a read-then-write, so two
    concurrent turns for the same user in the same window can never both slip past the cap. This
    lives in the app database (not Redis) on purpose: it is the source of truth a Free user's
    hard daily cap is enforced against, so it must survive a Redis flush — an ephemeral cache
    wipe must never silently re-grant spent quota.
    """

    __tablename__ = "usage_counters"

    user_id: Mapped[str] = mapped_column(primary_key=True)
    counter: Mapped[str] = mapped_column(primary_key=True)
    window_key: Mapped[str] = mapped_column(primary_key=True)
    n: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint("n >= 0", name="ck_usage_counters_nonneg"),
        Index("ix_usage_counters_user", "user_id"),
    )


class UsageEventRow(Base):
    """The durable per-turn usage rollup — the write-through target of a finished turn's
    `UsageAccumulator.snapshot()`. One row per completed turn: token/tool/cost totals for
    cost accounting and analytics. Distinct from `usage_counters` (the live enforcement gate):
    this is the append-only record of what a turn actually consumed."""

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(index=True)
    turn_id: Mapped[str]
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    tool_calls: Mapped[int] = mapped_column(default=0)
    finmind_calls: Mapped[int] = mapped_column(default=0)
    cost_micros: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        UniqueConstraint("user_id", "turn_id", name="uq_usage_events_user_turn"),
        Index("ix_usage_events_user_created", "user_id", "created_at"),
    )


# ---- Payments & subscriptions (Phase 9, Paddle) --------------------------------------------


class SubscriptionRow(Base):
    """The record of a user's Paddle subscription — the source of which `users.tier` is a derived
    cache. Written only by the billing webhook (and reconcile-on-read against the live Paddle
    API); the app never trusts a client-asserted tier. `updated_from_event_at` holds the
    `occurred_at` of the last webhook applied, so a late-delivered older event can be recognized
    and skipped rather than allowed to regress state. `scheduled_cancel_at` non-null means "the
    user asked to cancel, effective at that date" — the tier stays active until the actual
    `subscription.canceled` event fires."""

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    paddle_subscription_id: Mapped[str] = mapped_column(unique=True)
    paddle_customer_id: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str]
    price_id: Mapped[str]
    tier: Mapped[str]
    byok: Mapped[bool] = mapped_column(default=False)
    current_period_start: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    current_period_end: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    scheduled_cancel_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    updated_from_event_at: Mapped[datetime] = mapped_column(_UTC)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','trialing','past_due','paused','canceled')",
            name="ck_subscriptions_status",
        ),
        CheckConstraint("tier IN ('free','premium','pro')", name="ck_subscriptions_tier"),
        Index("ix_subscriptions_user", "user_id"),
    )


class BillingEventRow(Base):
    """The idempotency ledger for processed Paddle webhooks. The `paddle_event_id` unique
    constraint makes redelivery a no-op: a second insert of the same event collides and the
    handler treats it as already-processed. `raw_payload` is the verified envelope, kept for
    audit/debugging — it carries no card data (Paddle is the merchant of record)."""

    __tablename__ = "billing_events"

    id: Mapped[str] = mapped_column(primary_key=True)
    paddle_event_id: Mapped[str] = mapped_column(unique=True)
    event_type: Mapped[str]
    occurred_at: Mapped[datetime] = mapped_column(_UTC)
    processed_at: Mapped[datetime] = mapped_column(_UTC)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)

    __table_args__ = (Index("ix_billing_events_occurred", "occurred_at"),)


# ---- Themes, tiers & supply-chain (platform reference data — no user scope) ----------------


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


# ---- Turn feedback (minimal thumbs + comment capture — no score/alert pipeline) ------------


class TurnFeedbackRow(Base):
    """One user's 👍/👎 (+ optional short comment) on a turn. `UNIQUE(turn_id, user_id)` is the
    idempotent-resubmit guard: tapping thumbs-down then thumbs-up on the same turn updates this
    one row (`SqlAlchemyFeedbackStore.submit` upserts on that key) rather than accumulating
    duplicate rows. This is deliberately just a rating capture — no rolling quality score, no
    alert threshold, no admin endpoints; that pipeline is a dedicated follow-up, not built here.
    """

    __tablename__ = "turn_feedback"

    id: Mapped[str] = mapped_column(primary_key=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey(_TURNS_ID_FK, ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(index=True)
    rating: Mapped[str]
    comment: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    updated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint("rating IN ('up','down')", name="ck_turn_feedback_rating"),
        CheckConstraint(
            "comment IS NULL OR length(comment) <= 500", name="ck_turn_feedback_comment_len"
        ),
        UniqueConstraint("turn_id", "user_id", name="uq_turn_feedback_turn_user"),
    )


# ---- Multi-factor score cache (platform reference data — no user scope) --------------------


class StockScoreRow(Base):
    """One symbol's cached daily composite-score result for one horizon profile — the write
    target of the daily scoring batch and the read target of both the per-symbol detail-page
    lookup and the leaderboard. Unlike the theme tables above, this *does* carry a date
    (`as_of`): unlike industry classification, a score is genuinely a new value every trading
    day, so a fresh batch pass replaces (upserts) the prior row for the same
    `(symbol, market, profile)` rather than accumulating history — this table is a same-day
    snapshot cache, not a time series. No `user_id`: the composite is platform-wide, not
    per-user state.

    `symbol_type` discriminates which breakdown shape this row holds (`StockScoreBreakdown` vs
    `EtfScoreBreakdown`, `analytics/models.py`) — a plain equity and an ETF need genuinely
    different factors (an ETF has no ROE/EV-EBITDA of its own), so this table carries BOTH the
    five stock factor columns and the five ETF factor columns (all nullable); a stock row leaves
    the ETF columns null and vice versa. `momentum_*` is the one column name shared by both
    breakdowns (12-1 momentum is meaningful for both a stock and a fund) — every other factor
    name is shape-specific. Flat columns rather than one JSON blob for the same reason the
    original 5-column design chose them: each is independently point-looked-up-shaped, never a
    variable set, so flat columns are simpler and index-free reads faster than unpacking JSON on
    every read. `partial_factors` (a short list of factor names) is a JSON array per this
    project's SQLite/Postgres-portable JSON convention.

    Indexing: `uq_stock_scores_symbol_market_profile_as_of` is the write-side upsert key and also
    serves the detail-page point lookup (`WHERE symbol=? AND market=? AND profile=?` ordered by
    `as_of` desc, or filtered to a specific `as_of`); `ix_stock_scores_leaderboard` serves the
    leaderboard's `WHERE market=? AND profile=? AND as_of=? ORDER BY composite` sort-and-slice
    directly off the index, without a separate sort step.
    """

    __tablename__ = "stock_scores"

    id: Mapped[str] = mapped_column(primary_key=True)
    symbol: Mapped[str]
    market: Mapped[str]
    as_of: Mapped[str]  # ISO date string — portable, matches the theme layer's `as_of` convention
    profile: Mapped[str]
    symbol_type: Mapped[str] = mapped_column(default="stock")
    composite: Mapped[float | None] = mapped_column(default=None)
    band: Mapped[str]

    # Shared by both breakdowns — 12-1 momentum is meaningful for a stock AND a fund.
    momentum_percentile: Mapped[float | None] = mapped_column(default=None)
    momentum_description: Mapped[str] = mapped_column(default="")
    momentum_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    # -- Stock-only factors (StockScoreBreakdown) --
    trend_quality_percentile: Mapped[float | None] = mapped_column(default=None)
    trend_quality_description: Mapped[str] = mapped_column(default="")
    trend_quality_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    value_guard_percentile: Mapped[float | None] = mapped_column(default=None)
    value_guard_description: Mapped[str] = mapped_column(default="")
    value_guard_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    quality_percentile: Mapped[float | None] = mapped_column(default=None)
    quality_description: Mapped[str] = mapped_column(default="")
    quality_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    revenue_flow_percentile: Mapped[float | None] = mapped_column(default=None)
    revenue_flow_description: Mapped[str] = mapped_column(default="")
    revenue_flow_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    # -- US-stock-only factor (UsStockScoreBreakdown) — `analyst_revision` replaces `revenue_flow`
    # for Market.US rows (revenue_flow has no US data source; see that model's own docstring).
    # A US row leaves `revenue_flow_*` null and a TW row leaves `analyst_revision_*` null.
    analyst_revision_percentile: Mapped[float | None] = mapped_column(default=None)
    analyst_revision_description: Mapped[str] = mapped_column(default="")
    analyst_revision_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    growth_percentile: Mapped[float | None] = mapped_column(default=None)
    growth_description: Mapped[str] = mapped_column(default="")
    growth_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    liquidity_percentile: Mapped[float | None] = mapped_column(default=None)
    liquidity_description: Mapped[str] = mapped_column(default="")
    liquidity_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    # -- ETF-only factors (EtfScoreBreakdown) --
    premium_discount_percentile: Mapped[float | None] = mapped_column(default=None)
    premium_discount_description: Mapped[str] = mapped_column(default="")
    premium_discount_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    cost_percentile: Mapped[float | None] = mapped_column(default=None)
    cost_description: Mapped[str] = mapped_column(default="")
    cost_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    management_activity_percentile: Mapped[float | None] = mapped_column(default=None)
    management_activity_description: Mapped[str] = mapped_column(default="")
    management_activity_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    liquidity_flow_percentile: Mapped[float | None] = mapped_column(default=None)
    liquidity_flow_description: Mapped[str] = mapped_column(default="")
    liquidity_flow_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    partial_factors: Mapped[list[str]] = mapped_column(JSON, default=list)
    regime_caveat: Mapped[str] = mapped_column(default="")
    non_trading_caveat: Mapped[str] = mapped_column(default="")
    normalization_caveat: Mapped[str] = mapped_column(default="")

    computed_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(_MARKET_TW_US_CHECK, name="ck_stock_scores_market"),
        CheckConstraint("profile IN ('swing','position')", name="ck_stock_scores_profile"),
        CheckConstraint("band IN ('偏多','中性','偏空')", name="ck_stock_scores_band"),
        CheckConstraint(
            "symbol_type IN ('stock','etf','etf_leveraged','etf_inverse')",
            name="ck_stock_scores_symbol_type",
        ),
        UniqueConstraint(
            "symbol",
            "market",
            "profile",
            "as_of",
            name="uq_stock_scores_symbol_market_profile_as_of",
        ),
        Index(
            "ix_stock_scores_leaderboard",
            "market",
            "profile",
            "as_of",
            "composite",
        ),
    )


# ---- AI stock summary cache (platform reference data — no user scope) ----------------------


class StockSummaryRow(Base):
    """One symbol's cached AI-generated headline + narrative + SWOT — the write target of
    `analytics/summary_generation.py::generate_stock_summary` and the read target of the
    stock-detail-page `get_stock_summary` tool.

    `trade_date` is deliberately the *score's own* `ScoreResult.as_of` this summary was
    generated from, not "today" — matching `analytics.summary_store.get_cached_summary`'s point
    lookup by that exact date (never a "latest" fallback), so a summary generated against a
    still-cached prior day's score is never silently treated as fresh-today. Unlike
    `StockScoreRow`, this table carries no `market IN ('TW','US')` composite reuse concern beyond
    the same check, and no factor columns — the SWOT quadrants are portable JSON arrays of short
    strings (this project's SQLite/Postgres-portable JSON convention), not flat per-item columns,
    because the bullet count per quadrant varies call to call. No `user_id`: like the score cache,
    this is platform-wide derived content, not per-user state."""

    __tablename__ = "stock_summaries"

    id: Mapped[str] = mapped_column(primary_key=True)
    symbol: Mapped[str]
    market: Mapped[str]
    trade_date: Mapped[str]  # ISO date string — the ScoreResult.as_of this summary is grounded in
    headline: Mapped[str]
    narrative: Mapped[str]
    swot_strengths: Mapped[list[str]] = mapped_column(JSON, default=list)
    swot_weaknesses: Mapped[list[str]] = mapped_column(JSON, default=list)
    swot_opportunities: Mapped[list[str]] = mapped_column(JSON, default=list)
    swot_threats: Mapped[list[str]] = mapped_column(JSON, default=list)
    non_trading_caveat: Mapped[str] = mapped_column(default="")
    generated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(_MARKET_TW_US_CHECK, name="ck_stock_summaries_market"),
        UniqueConstraint(
            "symbol", "market", "trade_date", name="uq_stock_summaries_symbol_market_trade_date"
        ),
    )


# ---- Trend score (six-dimension composite — a real time series, unlike stock_scores) -------


class TrendScoreRow(Base):
    """One symbol's 趨勢評分 (trend score) composite for one trading day — the write target of
    `analytics/scoring_batch.py::job_trend_score_batch` and the read target of both the
    detail-page point lookup and the chart's ranged history read.

    Unlike `StockScoreRow` (a same-day snapshot cache the batch *replaces* daily), this table
    genuinely needs history: a caller reads a date RANGE for a chart
    (`analytics/trend_score_store.py::get_trend_score_history`), so every day's row is kept, not
    overwritten — `upsert_trend_score` still upserts by `(symbol, market, as_of)` so a same-day
    re-run doesn't accumulate duplicate rows for that one day, but distinct `as_of` days
    genuinely coexist.

    Flat per-dimension columns (`technical_*`/`chip_flow_*`/`margin_credit_*`/
    `broker_concentration_*`/`sector_rotation_*`/`taiex_correlation_*`, each a value/description/
    unavailable_reason triplet) — same flat-column reasoning `StockScoreRow`'s own docstring
    gives: each dimension is independently point-looked-up-shaped, never a variable set, so flat
    columns are simpler and index-free reads faster than unpacking JSON on every read.
    `unavailable_dimensions` (a short list of dimension names) is a JSON array per this project's
    SQLite/Postgres-portable JSON convention (backend/CLAUDE.md §6).

    Indexing: `uq_trend_scores_symbol_market_as_of` is the write-side upsert key and also serves
    the detail-page point lookup; `ix_trend_scores_symbol_market_as_of` (non-unique) serves the
    chart's `WHERE symbol=? AND market=? ORDER BY as_of` range scan directly off the index."""

    __tablename__ = "trend_scores"

    id: Mapped[str] = mapped_column(primary_key=True)
    symbol: Mapped[str]
    market: Mapped[str]
    as_of: Mapped[str]  # ISO date string — portable, matches stock_scores' own as_of convention
    composite: Mapped[float | None] = mapped_column(default=None)

    technical_value: Mapped[float | None] = mapped_column(default=None)
    technical_description: Mapped[str] = mapped_column(default="")
    technical_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    chip_flow_value: Mapped[float | None] = mapped_column(default=None)
    chip_flow_description: Mapped[str] = mapped_column(default="")
    chip_flow_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    margin_credit_value: Mapped[float | None] = mapped_column(default=None)
    margin_credit_description: Mapped[str] = mapped_column(default="")
    margin_credit_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    broker_concentration_value: Mapped[float | None] = mapped_column(default=None)
    broker_concentration_description: Mapped[str] = mapped_column(default="")
    broker_concentration_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    sector_rotation_value: Mapped[float | None] = mapped_column(default=None)
    sector_rotation_description: Mapped[str] = mapped_column(default="")
    sector_rotation_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    taiex_correlation_value: Mapped[float | None] = mapped_column(default=None)
    taiex_correlation_description: Mapped[str] = mapped_column(default="")
    taiex_correlation_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    # -- US-only dimensions — replace chip_flow/margin_credit/broker_concentration/sector_rotation
    # one-for-one for Market.US rows (none of those four TW dimensions has a US data source; see
    # `analytics/trend_score.py::US_DIMENSION_WEIGHTS`'s own comment). A US row leaves the four TW
    # columns above null; a TW row leaves these four null.
    institutional_ownership_value: Mapped[float | None] = mapped_column(default=None)
    institutional_ownership_description: Mapped[str] = mapped_column(default="")
    institutional_ownership_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    insider_sentiment_value: Mapped[float | None] = mapped_column(default=None)
    insider_sentiment_description: Mapped[str] = mapped_column(default="")
    insider_sentiment_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    analyst_revision_value: Mapped[float | None] = mapped_column(default=None)
    analyst_revision_description: Mapped[str] = mapped_column(default="")
    analyst_revision_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    sector_momentum_value: Mapped[float | None] = mapped_column(default=None)
    sector_momentum_description: Mapped[str] = mapped_column(default="")
    sector_momentum_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    unavailable_dimensions: Mapped[list[str]] = mapped_column(JSON, default=list)
    non_trading_caveat: Mapped[str] = mapped_column(default="")

    computed_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(_MARKET_TW_US_CHECK, name="ck_trend_scores_market"),
        UniqueConstraint(
            "symbol",
            "market",
            "as_of",
            name="uq_trend_scores_symbol_market_as_of",
        ),
        Index(
            "ix_trend_scores_symbol_market_as_of",
            "symbol",
            "market",
            "as_of",
        ),
    )


__all__ = [
    "AlertPreferenceRow",
    "AlertRuleRow",
    "BillingEventRow",
    "DevicePushTokenRow",
    "MemoryRow",
    "MessageRow",
    "NotificationRow",
    "OAuthAccountRow",
    "ScheduleJobRow",
    "SessionRow",
    "StockScoreRow",
    "StockSummaryRow",
    "StreamEventRow",
    "SubscriptionRow",
    "SupplyChainEdgeRow",
    "ThemeChangeLogRow",
    "ThemeMembershipRow",
    "ThemeRow",
    "ThemeTierRow",
    "ThreadRow",
    "TrendScoreRow",
    "TurnFeedbackRow",
    "TurnRow",
    "UsageCounterRow",
    "UsageEventRow",
    "UserPetRow",
    "UserPetStatsRow",
    "UserRow",
    "UserSecretRow",
    "WatchlistItemRow",
    "WatchlistRow",
]
