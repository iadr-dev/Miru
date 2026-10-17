"""The OAuth authorization server for MCP clients ("sign in with your Miru account").

Five tables. Codes and refresh tokens are stored only as SHA-256 hashes; the raw values exist in
the response that hands them to the client and nowhere else. `oauth_clients` and
`oauth_authorization_requests` are not user-owned (a client registers, and a request starts,
before anyone has signed in); the other three carry `user_id`, so account erasure and row-level
security cover them like every other user-owned table.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.models._common import USERS_ID_FK as _USERS_ID_FK
from db.models._common import UTC_TIMESTAMP as _UTC

_CLIENT_ID_FK = "oauth_clients.client_id"
_GRANT_ID_FK = "oauth_grants.id"


class OAuthClientRow(Base):
    """A registered public client. `dcr` rows come from RFC 7591 registration (client_id is a
    random id we mint); `cimd` rows cache a Client ID Metadata Document (client_id is the https
    URL it was fetched from), refreshed once `metadata_fetched_at` is older than the cache TTL."""

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    redirect_uris: Mapped[list[str]] = mapped_column(JSON)
    kind: Mapped[str]
    token_endpoint_auth_method: Mapped[str] = mapped_column(default="none")
    created_at: Mapped[datetime] = mapped_column(_UTC)
    last_seen_at: Mapped[datetime] = mapped_column(_UTC)
    metadata_fetched_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)

    __table_args__ = (
        CheckConstraint("kind IN ('dcr','cimd')", name="ck_oauth_clients_kind"),
        CheckConstraint(
            "token_endpoint_auth_method IN ('none')", name="ck_oauth_clients_auth_method"
        ),
        Index("ix_oauth_clients_kind_last_seen", "kind", "last_seen_at"),
    )


class OAuthAuthorizationRequestRow(Base):
    """One validated `/oauth/authorize` request waiting for the user's decision. `nonce` is the
    consent-screen CSRF token: only the signed-in user's GET returns it, and the decision must
    echo it. The row is deleted when the decision is taken (single use) or purged once expired."""

    __tablename__ = "oauth_authorization_requests"

    id: Mapped[str] = mapped_column(primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey(_CLIENT_ID_FK, ondelete="CASCADE"))
    redirect_uri: Mapped[str]
    state: Mapped[str | None] = mapped_column(default=None)
    code_challenge: Mapped[str]
    resource: Mapped[str]
    scope: Mapped[str]
    nonce: Mapped[str]
    # SHA-256 of the secret handed to the browser that opened /oauth/authorize (httpOnly cookie);
    # consent is only served to that browser.
    browser_hash: Mapped[str]
    # Whether the client sent redirect_uri (false = it relied on its single registered one).
    redirect_uri_supplied: Mapped[bool] = mapped_column(default=True, server_default="1")
    # The user who first opened the consent screen; nobody else may view or answer it. Not named
    # `user_id`: the row exists before any user does, so row-level security must not claim it.
    bound_user_id: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    expires_at: Mapped[datetime] = mapped_column(_UTC, index=True)


class OAuthAuthCodeRow(Base):
    """A one-time authorization code (60 s). `used_at` is set the instant it is redeemed; a second
    presentation revokes the grant the first one created (RFC 6749 section 4.1.2)."""

    __tablename__ = "oauth_auth_codes"

    code_hash: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey(_CLIENT_ID_FK, ondelete="CASCADE"))
    redirect_uri: Mapped[str]
    redirect_uri_supplied: Mapped[bool] = mapped_column(default=True, server_default="1")
    code_challenge: Mapped[str]
    resource: Mapped[str]
    scope: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(_UTC)
    expires_at: Mapped[datetime] = mapped_column(_UTC, index=True)
    used_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    grant_id: Mapped[str | None] = mapped_column(default=None)


class OAuthGrantRow(Base):
    """A user's standing consent for one client: the "connected app" the user can see and
    revoke. Created when a code is redeemed. Revoking it ends every token under it."""

    __tablename__ = "oauth_grants"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey(_CLIENT_ID_FK, ondelete="CASCADE"))
    scope: Mapped[str]
    resource: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(_UTC)
    last_used_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)


class OAuthRefreshTokenRow(Base):
    """One refresh token of a grant. Every use rotates it: the presented row gets `rotated_at`
    and a successor is written. Presenting a row that is already rotated is a replay (theft
    signal), which revokes the whole grant."""

    __tablename__ = "oauth_refresh_tokens"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(_USERS_ID_FK, ondelete="CASCADE"), index=True)
    grant_id: Mapped[str] = mapped_column(ForeignKey(_GRANT_ID_FK, ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    rotated_from: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(_UTC)
    expires_at: Mapped[datetime] = mapped_column(_UTC)
    rotated_at: Mapped[datetime | None] = mapped_column(_UTC, default=None)

    __table_args__ = (Index("ix_oauth_refresh_tokens_expires_at", "expires_at"),)
