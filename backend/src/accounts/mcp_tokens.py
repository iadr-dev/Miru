"""Access tokens for the MCP resource server (the OAuth tokens the authorization server issues).

A separate token type from the app's own session JWT (`accounts.auth`), on purpose:

- `typ = "mcp_access"` - `decode_access_token` rejects it, so it can never be used as a web
  session, and `verify_mcp_access_token` rejects the plain app JWT.
- `aud` = the exact MCP resource URL and `iss` = this app's issuer - a token minted for another
  resource (or another issuer) is refused, which is what stops a token being replayed across
  servers (MCP authorization spec, "token audience binding").
- It is signed with a key derived from the server secret for this one purpose, so even a
  mis-typed app JWT fails on the signature.
- It carries no tier or entitlement. Who the user is comes from `sub`; what they may read is
  resolved from the user row on every request.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt

from accounts.auth import AuthConfigError, require_jwt_secret
from config.settings import Settings

MCP_ACCESS_TYP = "mcp_access"
MCP_SCOPE_READ = "mcp:read"
SUPPORTED_SCOPES: tuple[str, ...] = (MCP_SCOPE_READ,)

_ALGORITHM = "HS256"
_LEEWAY_SECONDS = 5


class McpTokenError(Exception):
    """The token cannot authenticate a request. `reason` is the safe, coarse cause."""

    def __init__(self, reason: Literal["expired", "invalid"]) -> None:
        super().__init__(reason)
        self.reason: Literal["expired", "invalid"] = reason


@dataclass(frozen=True)
class McpAccessClaims:
    user_id: str
    client_id: str
    grant_id: str
    scope: str
    expires_at: datetime


def _signing_key(settings: Settings) -> str:
    secret = require_jwt_secret(settings)
    return hmac.new(secret.encode(), b"mcp-access-token", hashlib.sha256).hexdigest()


def issue_mcp_access_token(  # noqa: PLR0913 - one claim per keyword, all required
    *,
    user_id: str,
    client_id: str,
    grant_id: str,
    scope: str,
    issuer: str,
    resource: str,
    settings: Settings,
) -> tuple[str, int]:
    """A signed access token and its lifetime in seconds."""
    lifetime = timedelta(minutes=settings.oauth_access_token_minutes)
    now = datetime.now(UTC)
    claims = {
        "typ": MCP_ACCESS_TYP,
        "iss": issuer,
        "aud": resource,
        "sub": user_id,
        "scope": scope,
        "client_id": client_id,
        "gid": grant_id,
        "iat": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    token = jwt.encode(claims, _signing_key(settings), algorithm=_ALGORITHM)
    return token, int(lifetime.total_seconds())


def verify_mcp_access_token(
    token: str, *, settings: Settings, issuer: str, resource: str
) -> McpAccessClaims:
    """Check signature, expiry, type, issuer, audience and scope; raise `McpTokenError` if any
    fails. Never raises anything else and never returns a partial result."""
    try:
        key = _signing_key(settings)
    except AuthConfigError as exc:  # no signing secret: nobody can be authenticated
        raise McpTokenError("invalid") from exc
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[_ALGORITHM],
            audience=resource,
            issuer=issuer,
            leeway=_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise McpTokenError("expired") from exc
    except jwt.PyJWTError as exc:
        raise McpTokenError("invalid") from exc
    subject, client_id, grant_id = claims.get("sub"), claims.get("client_id"), claims.get("gid")
    scope, expires = claims.get("scope"), claims.get("exp")
    if (
        claims.get("typ") != MCP_ACCESS_TYP
        or not isinstance(subject, str)
        or not isinstance(client_id, str)
        or not isinstance(grant_id, str)
        or not isinstance(scope, str)
        or not isinstance(expires, int)
        or MCP_SCOPE_READ not in scope.split()
    ):
        raise McpTokenError("invalid")
    return McpAccessClaims(
        user_id=subject,
        client_id=client_id,
        grant_id=grant_id,
        scope=scope,
        expires_at=datetime.fromtimestamp(expires, UTC),
    )


__all__ = [
    "MCP_ACCESS_TYP",
    "MCP_SCOPE_READ",
    "SUPPORTED_SCOPES",
    "McpAccessClaims",
    "McpTokenError",
    "issue_mcp_access_token",
    "verify_mcp_access_token",
]
