"""JWT access tokens + the refresh-token session store (rotation + reuse detection).

Two pieces:
- `issue_access_token` / `decode_access_token` — short-lived (~15 min) HS256 JWTs held in
  memory by the client; the API verifies the `Bearer` token on every call. The signing secret
  comes from config (`JWT_SECRET_KEY`), never hardcoded.
- `SessionStore` — the `sessions` table backing the long-lived refresh token. Only a
  **hash** of the refresh token is stored (never the raw token). `rotate` issues a new token
  and invalidates the prior; presenting an already-rotated-out token is a theft signal that
  revokes the **whole family** (reuse detection).

The raw refresh token itself lives only in the client's `httpOnly` cookie — this module returns
it once from `create`/`rotate` for the caller to set as a cookie, and otherwise only ever sees
its hash.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

import jwt
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from config.settings import Settings
from db.models import SessionRow

logger = structlog.get_logger(__name__)


class AuthConfigError(RuntimeError):
    """Raised when `JWT_SECRET_KEY` is not configured — refuses to sign with a default."""


class InvalidAccessTokenError(RuntimeError):
    pass


class RefreshTokenReuseError(RuntimeError):
    """A rotated-out refresh token was presented again — theft signal; the whole session family
    is revoked."""


class RefreshTokenInvalidError(RuntimeError):
    pass


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes even for `DateTime(timezone=True)` columns; treat a naive
    value as UTC so comparisons against `datetime.now(UTC)` don't raise. Postgres returns
    tz-aware values already (no-op there)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _hash_token(raw_token: str) -> str:
    """SHA-256 of the raw refresh token — what `sessions.token_hash` stores. A refresh token is
    high-entropy random, so a plain fast hash (not a slow password KDF) is the right tool: it
    makes DB contents non-reversible without adding per-request cost."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _require_secret(settings: Settings) -> str:
    if not settings.jwt_secret_key:
        raise AuthConfigError("JWT_SECRET_KEY is not configured")
    return settings.jwt_secret_key.get_secret_value()


def require_jwt_secret(settings: Settings) -> str:
    """The server signing secret; raises `AuthConfigError` when unset (never an empty fallback)."""
    return _require_secret(settings)


def issue_access_token(*, user_id: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    secret = _require_secret(settings)
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_token_expire_minutes)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, *, settings: Settings | None = None) -> str:
    """Return the `sub` (user id) if the token is valid + unexpired, else raise."""
    settings = settings or get_settings()
    secret = _require_secret(settings)
    try:
        payload = jwt.decode(token, secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidAccessTokenError("access token invalid or expired") from exc
    subject = payload.get("sub")
    if payload.get("typ") != "access" or not isinstance(subject, str):
        # Other signed tokens (e.g. OAuth start tickets) are never a session.
        raise InvalidAccessTokenError("access token missing subject or wrong type")
    return subject


@runtime_checkable
class SessionStore(Protocol):
    async def create(
        self, *, user_id: str, user_agent: str | None = None, ip: str | None = None
    ) -> tuple[str, str]: ...

    async def rotate(self, *, raw_token: str) -> tuple[str, str]: ...

    async def revoke(self, *, raw_token: str) -> None: ...

    async def revoke_all(self, *, user_id: str) -> None: ...

    async def user_id_for(self, *, raw_token: str) -> str | None: ...


class SqlAlchemySessionStore:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    async def create(
        self, *, user_id: str, user_agent: str | None = None, ip: str | None = None
    ) -> tuple[str, str]:
        """Start a new session family. Returns `(session_id, raw_refresh_token)` — the raw
        token is returned exactly once, for the caller to set as an httpOnly cookie."""
        raw_token = secrets.token_urlsafe(32)
        family_id = str(uuid.uuid4())
        row = self._new_row(
            user_id=user_id,
            raw_token=raw_token,
            family_id=family_id,
            rotated_from=None,
            user_agent=user_agent,
            ip=ip,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("session_created", user_id=user_id, session_id=row.id)
        return row.id, raw_token

    async def rotate(self, *, raw_token: str) -> tuple[str, str]:
        """Exchange a valid refresh token for a fresh one, invalidating the old. Reuse of an
        already-rotated-out (revoked) token revokes the whole family."""
        row = await self._row_for(raw_token)
        if row is None:
            raise RefreshTokenInvalidError("refresh token not recognized")
        now = datetime.now(UTC)
        if row.revoked_at is not None:
            # Reuse of a rotated-out token — theft signal.
            await self._revoke_family(row.family_id)
            logger.warning(
                "refresh_token_reuse_detected", family_id=row.family_id, user_id=row.user_id
            )
            raise RefreshTokenReuseError("refresh token reuse detected — session family revoked")
        if _as_utc(row.expires_at) <= now:
            raise RefreshTokenInvalidError("refresh token expired")

        row.revoked_at = now
        new_raw = secrets.token_urlsafe(32)
        new_row = self._new_row(
            user_id=row.user_id,
            raw_token=new_raw,
            family_id=row.family_id,
            rotated_from=row.id,
            user_agent=row.user_agent,
            ip=row.ip,
        )
        self._session.add(new_row)
        await self._session.flush()
        logger.info("session_rotated", user_id=row.user_id, family_id=row.family_id)
        return new_row.id, new_raw

    async def revoke(self, *, raw_token: str) -> None:
        row = await self._row_for(raw_token)
        if row is not None and row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            await self._session.flush()
            logger.info("session_revoked", user_id=row.user_id, session_id=row.id)

    async def revoke_all(self, *, user_id: str) -> None:
        result = await self._session.execute(
            select(SessionRow).where(SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None))
        )
        now = datetime.now(UTC)
        for row in result.scalars().all():
            row.revoked_at = now
        await self._session.flush()
        logger.info("sessions_revoked_all", user_id=user_id)

    def _new_row(  # noqa: PLR0913 — one arg per persisted session dimension (mirrors the row shape)
        self,
        *,
        user_id: str,
        raw_token: str,
        family_id: str,
        rotated_from: str | None,
        user_agent: str | None,
        ip: str | None,
    ) -> SessionRow:
        now = datetime.now(UTC)
        return SessionRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            token_hash=_hash_token(raw_token),
            family_id=family_id,
            rotated_from=rotated_from,
            user_agent=user_agent,
            ip=ip,
            expires_at=now + timedelta(days=self._settings.jwt_refresh_token_expire_days),
            created_at=now,
        )

    async def user_id_for(self, *, raw_token: str) -> str | None:
        """The user id a (current) refresh token belongs to, or `None` if unrecognized. Lets a
        refresh handler recover the subject to mint a fresh access token without depending on the
        (likely-expired) access token still being decodable."""
        row = await self._row_for(raw_token)
        return row.user_id if row is not None else None

    async def _row_for(self, raw_token: str) -> SessionRow | None:
        result = await self._session.execute(
            select(SessionRow).where(SessionRow.token_hash == _hash_token(raw_token))
        )
        return result.scalar_one_or_none()

    async def _revoke_family(self, family_id: str) -> None:
        result = await self._session.execute(
            select(SessionRow).where(
                SessionRow.family_id == family_id, SessionRow.revoked_at.is_(None)
            )
        )
        now = datetime.now(UTC)
        for row in result.scalars().all():
            row.revoked_at = now
        await self._session.flush()


__all__ = [
    "AuthConfigError",
    "InvalidAccessTokenError",
    "RefreshTokenInvalidError",
    "RefreshTokenReuseError",
    "SessionStore",
    "SqlAlchemySessionStore",
    "decode_access_token",
    "issue_access_token",
    "require_jwt_secret",
]
