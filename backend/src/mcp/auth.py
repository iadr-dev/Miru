"""Who is calling the MCP endpoint: verification of the bearer credential.

The MCP server is an OAuth *resource server* (MCP authorization spec). It accepts one kind of
credential: an access token that this app's authorization server issued for THIS resource
(`accounts.mcp_tokens`), checked for signature, expiry, type, issuer and audience, and whose grant
the user has not revoked. It does not forward the token anywhere (no token passthrough).

The app's own session JWT carries no audience, so it was never issued for this server and is
refused - unless `MCP_ACCEPT_APP_ACCESS_TOKEN` is switched on, a development escape hatch for
calling the endpoint with a hand-minted token.

The verdict is only "which user, if any". What that user may read is decided per request from the
user row (`mcp.accounts_resolver`), never from the token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accounts.auth import AuthConfigError, InvalidAccessTokenError, decode_access_token
from accounts.mcp_oauth.service import grant_is_active
from accounts.mcp_tokens import McpTokenError, verify_mcp_access_token
from config.settings import Settings
from db import rls

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TokenVerdict:
    """`user_id` is set when the credential authenticates; otherwise `failure` says why."""

    user_id: str | None
    failure: Literal["invalid", "expired"] | None = None


class McpTokenVerifier:
    def __init__(
        self, *, settings: Settings, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory

    async def verify(self, token: str) -> TokenVerdict:
        failure: Literal["invalid", "expired"] = "invalid"
        issuer, resource = self._settings.oauth_issuer_url(), self._settings.mcp_resource_url()
        if issuer is not None and resource is not None:
            try:
                claims = verify_mcp_access_token(
                    token, settings=self._settings, issuer=issuer, resource=resource
                )
            except McpTokenError as exc:
                failure = exc.reason
            else:
                if await self._grant_stands(claims.grant_id, claims.user_id):
                    return TokenVerdict(user_id=claims.user_id)
                logger.info("mcp_token_grant_revoked")
                return TokenVerdict(user_id=None, failure="invalid")
        if self._settings.mcp_accept_app_access_token:
            try:
                return TokenVerdict(user_id=decode_access_token(token, settings=self._settings))
            except (InvalidAccessTokenError, AuthConfigError):
                pass
        return TokenVerdict(user_id=None, failure=failure)

    async def _grant_stands(self, grant_id: str, user_id: str) -> bool:
        # The signature already proved `user_id`; binding it lets row-level security allow the read.
        rls.bind_user(user_id)
        async with self._session_factory() as session:
            await rls.bind_session(session)
            return await grant_is_active(session, grant_id=grant_id, user_id=user_id)


__all__ = ["McpTokenVerifier", "TokenVerdict"]
