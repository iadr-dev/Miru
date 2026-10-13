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

import structlog
from fastapi import Depends, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accounts.auth import InvalidAccessTokenError, decode_access_token
from accounts.hooks import EntitlementResolver
from accounts.models import User
from accounts.store import SqlAlchemyUserStore, UserNotFoundError, UserStore
from api.auth_expired import AUTH_EXPIRED_STATE_KEY
from api.errors import ApiError
from api.turn_registry import TurnRegistry
from config.settings import Settings
from db import rls
from events.factory import EventBusFactory
from events.live_quote import QuoteFeed
from events.live_quote_factory import DataUpdateBroker
from events.rate_limit import AsyncRateLimiter
from events.turn_control import TurnControl
from orchestrator.orchestrator import Orchestrator
from payments.service import BillingService
from payments.store import SqlAlchemySubscriptionStore
from personality.registry import PersonalityRegistry
from skills.registry import SkillRegistry
from tools.base import ToolContext, dispatch_tool
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
    turn_control: TurnControl


async def system_scope() -> AsyncGenerator[None]:
    """Route dependency for the handful of endpoints that act before (or without) a signed-in
    user - the sign-in flow, inbound webhooks, token-link pages. Inside it the database does not
    filter by user (db/rls.py); keep it off every route that serves a signed-in user."""
    with rls.system_access():
        yield


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


async def _load_user(
    session: AsyncSession, settings: Settings, store: UserStore, user_id: str
) -> User:
    """Load the user, dropping a paid tier whose payment has been failing past the grace window -
    the one entitlement change that no webhook announces (it is the absence of a payment)."""
    user = await store.get(user_id=user_id)
    structlog.contextvars.bind_contextvars(user_id=user_id)  # on every log line of this request
    rls.bind_user(user_id)  # the database filters this request's queries to this user (db/rls.py)
    await rls.bind_session(session)  # ... including the transaction that is already open
    if user.tier.value == "free":
        return user
    return await BillingService(
        store=SqlAlchemySubscriptionStore(session),
        user_store=store,
        settings=settings,
    ).lapse_if_overdue(user)


async def optional_user(
    request: Request,
    state: AppState = Depends(get_state),
    store: UserStore = Depends(get_user_store),
    session: AsyncSession = Depends(get_session),
) -> User | None:
    """Resolve the caller to a `User`, or `None` if unauthenticated — never raises on a
    missing/invalid credential (public routes tolerate anonymous callers). A token that was
    presented but has expired is still served as anonymous, but flagged on the request so
    `AuthExpiredHeaderMiddleware` tells the client to refresh and retry; otherwise a signed-in
    paid user is silently served free-tier teasers (see `api/auth_expired.py`)."""
    token = _extract_token(request)
    if not token:
        return None
    try:
        user_id = decode_access_token(token, settings=state.settings)
    except InvalidAccessTokenError:
        setattr(request.state, AUTH_EXPIRED_STATE_KEY, True)
        return None
    try:
        return await _load_user(session, state.settings, store, user_id)
    except UserNotFoundError:
        return None


async def optional_user_released(
    request: Request,
    state: AppState = Depends(get_state),
) -> User | None:
    """`optional_user` for responses that stay open for a long time (SSE).

    The request-scoped `get_session` commits and closes only when the response ends, and a stream
    ends when the tab closes - hours later. Resolving the caller on that session would pin one
    pooled connection, and leave a transaction open and "idle", for every open tab. Here the user
    is loaded on its own short session that is committed and closed before this returns, so the
    connection goes straight back to the pool and the stream itself holds nothing."""
    token = _extract_token(request)
    if not token:
        return None
    try:
        user_id = decode_access_token(token, settings=state.settings)
    except InvalidAccessTokenError:
        setattr(request.state, AUTH_EXPIRED_STATE_KEY, True)
        return None
    async with state.session_factory() as session:
        try:
            user = await _load_user(session, state.settings, SqlAlchemyUserStore(session), user_id)
            await session.commit()
        except UserNotFoundError:
            await session.rollback()
            return None
    return user


async def require_user(
    request: Request,
    state: AppState = Depends(get_state),
    store: UserStore = Depends(get_user_store),
    session: AsyncSession = Depends(get_session),
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
        return await _load_user(session, state.settings, store, user_id)
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
    consumes. Entitlement is resolved fresh per request, never cached across requests — a tier
    change takes effect on the next request."""
    return state.entitlement_resolver.tool_context_for(user)


# The numeric literal, matching `api/errors.py`'s own convention — the `status.HTTP_422_*`
# constant's name churns across Starlette versions (entity → content) and emits a deprecation
# warning; the code is stable.
_HTTP_422_UNPROCESSABLE = 422


async def dispatch_validated_tool(
    state: AppState, tool_name: str, args: dict[str, object], ctx: ToolContext
) -> dict[str, object]:
    """Look up `tool_name`, validate `args` against its `input_model`, dispatch it, and return
    the JSON-ready result — the one `_dispatch` body every REST router (`market_rest.py`,
    `fund_data.py`, ...) used to duplicate verbatim. A bad tool arg that fails a cross-field
    validator (as opposed to a bad query PARAM, which FastAPI's own `RequestValidationError`
    handler already renders as 422) becomes a 422 `invalid_request` `ApiError`, never falls
    through to the catch-all 500 handler."""
    tool = state.tool_registry.get_tool(tool_name)
    if tool is None:  # pragma: no cover — registry always has these; guards a wiring regression
        raise ApiError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="tool_unavailable",
            message=f"Tool {tool_name} is not registered.",
            retryable=False,
        )
    try:
        validated = tool.input_model.model_validate(args)
    except ValidationError as exc:
        raise ApiError(
            status_code=_HTTP_422_UNPROCESSABLE,
            code="invalid_request",
            message=f"Request failed validation: {exc.errors()[0]['msg']}"
            if exc.errors()
            else "Request failed validation.",
            retryable=False,
        ) from exc
    result = await dispatch_tool(tool, validated, ctx)
    return result.model_dump(mode="json")


__all__ = [
    "ACCESS_TOKEN_COOKIE",
    "AppState",
    "dispatch_validated_tool",
    "get_session",
    "get_settings_dep",
    "get_state",
    "get_user_store",
    "optional_user",
    "require_user",
    "tool_context_for",
]
