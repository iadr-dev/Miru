"""phase9 accounts personality pets

Revision ID: 8c5e740b8f90
Revises: d4ea6dc975c3
Create Date: 2026-08-04 16:25:52.337965

Adds the Phase-9 product-layer schema: `users` (identity + `tier` cache + `personality_id`),
`oauth_accounts` (multi-provider linking), `user_secrets` (encrypted BYOK/channel tokens),
`sessions` (refresh-token rotation + reuse detection), and the cosmetic pet tables
(`user_pets`/`user_pet_stats`).

Dialect-aware: the partial-unique indexes (email-where-not-null, one-equipped-pet-per-user)
are expressed with the `sqlite_where`/`postgresql_where` kwargs, matching the pattern the
Phase-7 migration used for `turns`' "one running turn per thread" and the watchlist group-name
constraint. Payments tables (`subscriptions`/`billing_events`) are intentionally NOT created
here — Paddle billing is a later phase; `users.tier` defaults to `free` and is a derived cache
that phase will populate.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c5e740b8f90"
down_revision: str | None = "d4ea6dc975c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("picture_url", sa.String(), nullable=True),
        sa.Column("birthday", sa.String(), nullable=True),
        sa.Column("phone_country_code", sa.String(), nullable=True),
        sa.Column("phone_number", sa.String(), nullable=True),
        sa.Column("nationality", sa.String(), nullable=True),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("personality_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("tier IN ('free','premium','pro')", name="ck_users_tier"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Partial-unique: at most one non-null email across users — dialect-specific partial-index
    # DDL, not expressible via a portable ORM-level constraint (LINE users may have no email).
    op.create_index(
        "uq_users_email",
        "users",
        ["email"],
        unique=True,
        sqlite_where=sa.text("email IS NOT NULL"),
        postgresql_where=sa.text("email IS NOT NULL"),
    )

    op.create_table(
        "oauth_accounts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_account_id", sa.String(), nullable=False),
        sa.Column("email_at_provider", sa.String(), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("provider IN ('google','line')", name="ck_oauth_accounts_provider"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "provider_account_id", name="uq_oauth_accounts_provider_identity"
        ),
    )
    op.create_index(op.f("ix_oauth_accounts_user_id"), "oauth_accounts", ["user_id"], unique=False)

    op.create_table(
        "user_secrets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('llm_key','finmind_token','channel_token','asr_key','tts_key')",
            name="ck_user_secrets_kind",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "kind", "provider", name="uq_user_secrets_target"),
    )
    op.create_index(op.f("ix_user_secrets_user_id"), "user_secrets", ["user_id"], unique=False)

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("family_id", sa.String(), nullable=False),
        sa.Column("rotated_from", sa.String(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_sessions_family_id"), "sessions", ["family_id"], unique=False)
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"], unique=False)

    op.create_table(
        "user_pets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("pet_id", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.Column("obtained_via", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("obtained_via IN ('pull','admin')", name="ck_user_pets_obtained_via"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_pets_user_id"), "user_pets", ["user_id"], unique=False)
    op.create_index("ix_user_pets_user_active", "user_pets", ["user_id", "is_active"], unique=False)
    # Partial-unique: at most one equipped pet per user — dialect-specific partial-index DDL.
    op.create_index(
        "uq_user_pets_one_active_per_user",
        "user_pets",
        ["user_id"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "user_pet_stats",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("total_pulls", sa.Integer(), nullable=False),
        sa.Column("streak_days", sa.Integer(), nullable=False),
        sa.Column("last_login_date", sa.String(), nullable=True),
        sa.Column("chat_count", sa.Integer(), nullable=False),
        sa.Column("available_pulls", sa.Integer(), nullable=False),
        sa.Column("pity_counter", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_pet_stats")
    op.drop_index("uq_user_pets_one_active_per_user", table_name="user_pets")
    op.drop_index("ix_user_pets_user_active", table_name="user_pets")
    op.drop_index(op.f("ix_user_pets_user_id"), table_name="user_pets")
    op.drop_table("user_pets")
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_index(op.f("ix_sessions_family_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_user_secrets_user_id"), table_name="user_secrets")
    op.drop_table("user_secrets")
    op.drop_index(op.f("ix_oauth_accounts_user_id"), table_name="oauth_accounts")
    op.drop_table("oauth_accounts")
    op.drop_index("uq_users_email", table_name="users")
    op.drop_table("users")
