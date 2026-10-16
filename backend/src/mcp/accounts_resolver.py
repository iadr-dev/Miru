"""The production principal resolver: the application's own signed access JWT -> the real user's
tier, so an MCP caller sees exactly the data (and masked teasers) the same account sees in the
app.

`Authorization: Bearer <access JWT>` and `x-api-key: <access JWT>` both land here. There is no
separate API-key store yet; the `x-api-key` header accepts the same signed token so clients that
can only set a custom header (some Claude Code / Messages API setups) work too. An unrecognized
credential resolves to `None`, i.e. anonymous `Tier.FREE`.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accounts.auth import AuthConfigError, InvalidAccessTokenError, decode_access_token
from accounts.entitlements import finmind_class_for
from accounts.store import SqlAlchemyUserStore, UserNotFoundError
from config.settings import Settings
from db import rls
from payments.service import BillingService
from payments.store import SqlAlchemySubscriptionStore
from schemas.enums import Tier

logger = structlog.get_logger(__name__)


def verify_access_token(token: str, settings: Settings) -> str | None:
    """The user id a valid, unexpired access JWT names, else `None` (also `None` when the server
    has no signing secret, so a deployment without one simply never authenticates anyone)."""
    try:
        return decode_access_token(token, settings=settings)
    except (InvalidAccessTokenError, AuthConfigError):
        return None


class AccountsPrincipalResolver:
    def __init__(
        self, *, settings: Settings, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory

    async def resolve(self, api_key: str) -> tuple[str, Tier] | None:
        return await self.resolve_bearer(api_key)

    async def resolve_bearer(self, token: str) -> tuple[str, Tier] | None:
        user_id = verify_access_token(token, self._settings)
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
        return user.id, finmind_class_for(user)


__all__ = ["AccountsPrincipalResolver", "verify_access_token"]
