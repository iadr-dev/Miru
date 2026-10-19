"""Users, sign-in identities, encrypted secrets, sessions and the pet companion."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from db.models._common import CASCADE_DELETE_ORPHAN as _CASCADE_DELETE_ORPHAN
from db.models._common import USERS_ID_FK as _USERS_ID_FK
from db.models._common import UTC_TIMESTAMP as _UTC


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
    # BYOK users' saved default main model ("provider/model"); None = the provider default.
    default_model: Mapped[str | None] = mapped_column(default=None)
    # The user's switch for long-term memory: off = nothing is recalled into a turn and
    # nothing new is saved (existing memories are kept, not deleted).
    memory_enabled: Mapped[bool] = mapped_column(default=True, server_default="1")
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
    account). The same provider identity may be linked to several users (e.g. one LINE account on
    a main and a second Miru account); each user then shows it as linked. Signing in with such an
    identity opens the account that linked it first."""

    __tablename__ = "oauth_accounts"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    provider: Mapped[str]
    provider_account_id: Mapped[str]
    email_at_provider: Mapped[str | None] = mapped_column(default=None)
    linked_at: Mapped[datetime] = mapped_column(_UTC)

    user: Mapped[UserRow] = relationship(back_populates="oauth_accounts")

    __table_args__ = (
        CheckConstraint(
            "provider IN ('google','line','telegram')", name="ck_oauth_accounts_provider"
        ),
        UniqueConstraint(
            "user_id",
            "provider",
            "provider_account_id",
            name="uq_oauth_accounts_user_provider_identity",
        ),
        Index("ix_oauth_accounts_identity", "provider", "provider_account_id"),
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
    # A masked preview (first 6 + last 4 characters) so the settings page can show WHICH key is
    # saved without ever returning it. Null for rows saved before this column existed.
    hint: Mapped[str | None] = mapped_column(default=None)
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
        # One row per (user, catalog pet): a duplicate pull converts to XP, so two rows for the same
        # pet can only come from a race, and `_owned_pet_row` would then raise on every later pull.
        UniqueConstraint("user_id", "pet_id", name="uq_user_pets_user_pet"),
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
    # Daily chat-XP cap: the Taipei day the counter belongs to, and the XP granted on it.
    xp_day: Mapped[str | None] = mapped_column(default=None)
    xp_today: Mapped[int] = mapped_column(default=0, server_default="0")
    # Whether the equipped pet may flavour replies (off until the user opts in).
    hint_enabled: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Whether the companion avatar and its notices show in chat (on by default).
    companion_visible: Mapped[bool] = mapped_column(default=True, server_default="1")
