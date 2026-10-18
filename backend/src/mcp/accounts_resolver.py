"""The production principal resolver: a verified MCP credential -> the real user's tier, so an MCP
caller sees exactly the data (and masked teasers) the same account sees in the app.

`Authorization: Bearer <access token>` and `x-api-key: <access token>` both land here. There is no
separate API-key store; `x-api-key` carries the same token for clients that can only set a custom
header. The credential is verified by `mcp.auth.McpTokenVerifier` (OAuth access token for this
resource; the app session JWT only behind the dev flag). An unrecognized credential resolves to
`None`, i.e. anonymous `Tier.FREE` (the gateway has already answered 401 where sign-in is required).

The tier is read from the user row on every call - it is never taken from the token, so a plan
change or a lapsed payment applies from the next request.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accounts.store import SqlAlchemyUserStore, UserNotFoundError
from config.settings import Settings
from db import rls
from mcp.auth import McpTokenVerifier
from payments.service import BillingService
from payments.store import SqlAlchemySubscriptionStore
from schemas.enums import Tier

logger = structlog.get_logger(__name__)


class AccountsPrincipalResolver:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        verifier: McpTokenVerifier | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._verifier = verifier or McpTokenVerifier(
            settings=settings, session_factory=session_factory
        )

    async def resolve(self, api_key: str) -> tuple[str, Tier] | None:
        return await self.resolve_bearer(api_key)

    async def resolve_bearer(self, token: str) -> tuple[str, Tier] | None:
        user_id = (await self._verifier.verify(token)).user_id
        if user_id is None:
            return None
        rls.bind_user(user_id)
        async with self._session_factory() as session:
            await rls.bind_session(session)
            store = SqlAlchemyUserStore(session)
            try:
                user = await store.get(user_id=user_id)
            except UserNotFoundError:
                return None
            if user.tier is not Tier.FREE:
                # The one entitlement change no webhook announces: a payment that kept failing
                # past the grace window. Same rule the API applies on every request.
                user = await BillingService(
                    store=SqlAlchemySubscriptionStore(session),
                    user_store=store,
                    settings=self._settings,
                ).lapse_if_overdue(user)
            await session.commit()
        return user.id, user.tier


__all__ = ["AccountsPrincipalResolver"]
