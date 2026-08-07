"""Application state container + the FastAPI dependency callables every router shares.

`AppState` is built once at app-creation time and stashed on `app.state`; it holds the long-lived
collaborators (settings, the DB session factory, the tool + skill registries, the orchestrator,
the entitlement resolver). Nothing per-request lives here — a DB session, the current user, and
the per-call `ToolContext` are all resolved per request by the dependency functions below.

The current-user dependency accepts either an `Authorization: Bearer <jwt>` header or the
session's access-token cookie, decodes it via `accounts.auth`, and loads the `User` through the
`UserStore`. `require_user` raises a 401 `ApiError` when no valid credential is present;
`optional_user` returns `None` instead — public reads use the latter so an anonymous caller still
gets `public` data (and a masked teaser for anything `sponsor`).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass

from fastapi import Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accounts.auth import InvalidAccessTokenError, decode_access_token
from accounts.hooks import EntitlementResolver
from accounts.models import User
from accounts.store import SqlAlchemyUserStore, UserNotFoundError, UserStore
from api.errors import ApiError
from api.turn_registry import TurnRegistry
from config.settings import Settings
from events.factory import EventBusFactory
from events.live_quote import QuoteFeed
from events.live_quote_factory import DataUpdateBroker
from events.rate_limit import AsyncRateLimiter
from orchestrator.orchestrator import Orchestrator
from personality.registry import PersonalityRegistry
from skills.registry import SkillRegistry
from tools.base import ToolContext
from tools.registry import ToolRegistry
from usage.store import UsageStore

ACCESS_TOKEN_COOKIE = "access_token"
_BEARER_PREFIX = "Bearer "


@dataclass(frozen=True)
class AppState:
    """The long-lived collaborators shared across requests, assembled once at app creation."""

    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    tool_registry: ToolRegistry
    skill_registry: SkillRegistry
    orchestrator: Orchestrator
    entitlement_resolver: EntitlementResolver
    personality_registry: PersonalityRegistry
    turn_registry: TurnRegistry
    event_bus_factory: EventBusFactory
    rate_limiter: AsyncRateLimiter
    usage_store: UsageStore
    quote_feed: QuoteFeed
    data_update_broker: DataUpdateBroker


def get_state(request: Request) -> AppState:
    state = getattr(request.app.state, "app_state", None)
    if not isinstance(state, AppState):  # pragma: no cover — misconfigured app, never in normal use
        raise ApiError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="Application state is not configured.",
            retryable=False,
        )
    return state


def get_settings_dep(state: AppState = Depends(get_state)) -> Settings:
    return state.settings


async def get_session(
    state: AppState = Depends(get_state),
) -> AsyncGenerator[AsyncSession]:
    """One transaction per request. Commits on a clean return, rolls back on any raised error —
    the per-request unit-of-work boundary the store layer (which only ever `flush`es) relies on."""
    async with state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_user_store(session: AsyncSession = Depends(get_session)) -> UserStore:
    return SqlAlchemyUserStore(session)


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.startswith(_BEARER_PREFIX):
        return header[len(_BEARER_PREFIX) :].strip() or None
    return request.cookies.get(ACCESS_TOKEN_COOKIE)


async def optional_user(
    request: Request,
    state: AppState = Depends(get_state),
    store: UserStore = Depends(get_user_store),
) -> User | None:
    """Resolve the caller to a `User`, or `None` if unauthenticated — never raises on a
    missing/invalid credential (public routes tolerate anonymous callers)."""
    token = _extract_token(request)
    if not token:
        return None
    try:
        user_id = decode_access_token(token, settings=state.settings)
        return await store.get(user_id=user_id)
    except (InvalidAccessTokenError, UserNotFoundError):
        return None


async def require_user(
    request: Request,
    state: AppState = Depends(get_state),
    store: UserStore = Depends(get_user_store),
) -> User:
    """Resolve the caller to a `User` or raise 401 — for routes that require sign-in (AI chat,
    anything user-scoped)."""
    token = _extract_token(request)
    if not token:
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthenticated",
            message="Sign-in required.",
            retryable=False,
        )
    try:
        user_id = decode_access_token(token, settings=state.settings)
        return await store.get(user_id=user_id)
    except (InvalidAccessTokenError, UserNotFoundError) as exc:
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthenticated",
            message="Invalid or expired credential.",
            retryable=False,
        ) from exc


def tool_context_for(
    user: User | None,
    state: AppState = Depends(get_state),
) -> ToolContext:
    """Build the per-request `ToolContext` (entitlement + TLS policy) the tool-dispatch gate
    consumes. Entitlement is resolved fresh per request, never cached — a mid-session tier change
    takes effect on the next call."""
    return state.entitlement_resolver.tool_context_for(user)


__all__ = [
    "ACCESS_TOKEN_COOKIE",
    "AppState",
    "get_session",
    "get_settings_dep",
    "get_state",
    "get_user_store",
    "optional_user",
    "require_user",
    "tool_context_for",
]
