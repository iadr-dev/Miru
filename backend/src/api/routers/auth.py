"""OAuth login/callback + session lifecycle + the current user's profile & connections.

Wires the already-built `accounts/oauth_flow.py` (`start_authorization`/`complete_authorization`)
to HTTP. The per-request CSRF `state` (plus PKCE `code_verifier` and OIDC `nonce` for LINE) is
stashed in short-lived `httpOnly` cookies between the begin and callback legs — the transient
flow-state store the flow module expects a caller to own. On a successful callback we issue the
short-lived access-token cookie + the long-lived rotating refresh cookie via `accounts.auth`.

`sponsor` masking and tiers are not this router's concern — it only establishes identity; the
resolved tier flows through `entitlements`/`ToolContext` on every downstream call.
"""

from __future__ import annotations

from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.auth import (
    RefreshTokenInvalidError,
    RefreshTokenReuseError,
    SessionStore,
    SqlAlchemySessionStore,
    issue_access_token,
)
from accounts.entitlements import entitlement_view
from accounts.models import EntitlementView, OAuthAccount, OAuthProvider, User
from accounts.oauth_flow import (
    StateMismatchError,
    complete_authorization,
    line_retry_authorization_url,
    start_authorization,
)
from accounts.oauth_providers import (
    IdentityVerificationError,
    OAuthConfigError,
    TokenExchangeError,
)
from accounts.store import (
    LastLoginMethodError,
    ProviderAlreadyLinkedError,
    UserStore,
)
from api.dependencies import (
    ACCESS_TOKEN_COOKIE,
    AppState,
    get_session,
    get_state,
    get_user_store,
    optional_user,
    require_user,
)
from api.errors import ApiError
from config.settings import Settings

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["accounts"])

REFRESH_TOKEN_COOKIE = "refresh_token"  # noqa: S105 — cookie name, not a secret
_STATE_COOKIE = "oauth_state"
_VERIFIER_COOKIE = "oauth_code_verifier"
_NONCE_COOKIE = "oauth_nonce"
_LINK_COOKIE = "oauth_link_user"
_FLOW_COOKIE_MAX_AGE = 600  # the begin→callback round-trip window (10 min)


def _session_store(session: AsyncSession = Depends(get_session)) -> SessionStore:
    return SqlAlchemySessionStore(session)


def _secure_cookies(settings: Settings) -> bool:
    """`Secure` cookies in production; relaxed in local dev so the flow works over plain HTTP."""
    return settings.env == "production"


def _set_flow_cookies(
    response: Response,
    settings: Settings,
    *,
    state: str,
    code_verifier: str | None,
    nonce: str | None,
    link_user_id: str | None,
) -> None:
    secure = _secure_cookies(settings)

    def _set(name: str, value: str) -> None:
        response.set_cookie(
            name,
            value,
            httponly=True,
            secure=secure,
            samesite="lax",
            max_age=_FLOW_COOKIE_MAX_AGE,
        )

    _set(_STATE_COOKIE, state)
    if code_verifier is not None:
        _set(_VERIFIER_COOKIE, code_verifier)
    if nonce is not None:
        _set(_NONCE_COOKIE, nonce)
    if link_user_id is not None:
        _set(_LINK_COOKIE, link_user_id)


def _clear_flow_cookies(response: Response) -> None:
    for name in (_STATE_COOKIE, _VERIFIER_COOKIE, _NONCE_COOKIE, _LINK_COOKIE):
        response.delete_cookie(name)


def _issue_session_cookies(
    response: Response, settings: Settings, *, user_id: str, refresh_token: str
) -> None:
    secure = _secure_cookies(settings)
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        issue_access_token(user_id=user_id, settings=settings),
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.jwt_access_token_expire_minutes * 60,
    )
    response.set_cookie(
        REFRESH_TOKEN_COOKIE,
        refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.jwt_refresh_token_expire_days * 86_400,
    )


def _parse_provider(provider: str) -> OAuthProvider:
    try:
        return OAuthProvider(provider)
    except ValueError as exc:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="unknown_provider",
            message=f"Unknown OAuth provider {provider!r}.",
            retryable=False,
        ) from exc


class BeginAuthResponse(BaseModel):
    authorization_url: str
    retry_authorization_url: str | None = None


@router.post("/auth/oauth/{provider}")
async def begin_oauth(
    provider: str,
    response: Response,
    state: AppState = Depends(get_state),
) -> BeginAuthResponse:
    """Begin sign-in: generate CSRF `state` (+ PKCE/nonce for LINE), stash them in httpOnly
    cookies, and return the provider authorize URL for the client to navigate to."""
    oauth_provider = _parse_provider(provider)
    try:
        auth_request = start_authorization(oauth_provider, state.settings)
    except OAuthConfigError as exc:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="provider_unconfigured",
            message=f"{oauth_provider.value} sign-in is not configured.",
            retryable=False,
        ) from exc
    _set_flow_cookies(
        response,
        state.settings,
        state=auth_request.state,
        code_verifier=auth_request.code_verifier,
        nonce=auth_request.nonce,
        link_user_id=None,
    )
    retry_url = (
        line_retry_authorization_url(auth_request.authorization_url)
        if oauth_provider is OAuthProvider.LINE
        else None
    )
    return BeginAuthResponse(
        authorization_url=auth_request.authorization_url,
        retry_authorization_url=retry_url,
    )


@router.post("/auth/oauth/{provider}/link")
async def begin_oauth_link(
    provider: str,
    response: Response,
    user: User = Depends(require_user),
    state: AppState = Depends(get_state),
) -> BeginAuthResponse:
    """Begin linking a provider to the already-signed-in user — same as begin, but the callback
    will attach the verified identity to the current account instead of logging in fresh."""
    oauth_provider = _parse_provider(provider)
    try:
        auth_request = start_authorization(oauth_provider, state.settings)
    except OAuthConfigError as exc:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="provider_unconfigured",
            message=f"{oauth_provider.value} sign-in is not configured.",
            retryable=False,
        ) from exc
    _set_flow_cookies(
        response,
        state.settings,
        state=auth_request.state,
        code_verifier=auth_request.code_verifier,
        nonce=auth_request.nonce,
        link_user_id=user.id,
    )
    return BeginAuthResponse(authorization_url=auth_request.authorization_url)


class MeResponse(BaseModel):
    user: User
    entitlements: EntitlementView


@router.get("/auth/oauth/{provider}/callback")
async def oauth_callback(  # noqa: PLR0913 — the callback carries the request + stored verifiers + DI seams
    provider: str,
    request: Request,
    response: Response,
    code: str = Query(...),
    oauth_state: str = Query(..., alias="state"),
    state: AppState = Depends(get_state),
    store: UserStore = Depends(get_user_store),
    session_store: SessionStore = Depends(_session_store),
) -> MeResponse:
    """OAuth redirect target: verify `state`, exchange the code, verify the ID token, resolve the
    identity (login/signup or link), then issue the session cookies."""
    oauth_provider = _parse_provider(provider)
    stored_state = request.cookies.get(_STATE_COOKIE)
    stored_verifier = request.cookies.get(_VERIFIER_COOKIE)
    stored_nonce = request.cookies.get(_NONCE_COOKIE)
    link_user_id = request.cookies.get(_LINK_COOKIE)

    try:
        user = await complete_authorization(
            oauth_provider,
            code,
            oauth_state,
            stored_state or "",
            store=store,
            settings=state.settings,
            stored_verifier=stored_verifier,
            stored_nonce=stored_nonce,
            current_user_id=link_user_id,
        )
    except StateMismatchError as exc:
        _clear_flow_cookies(response)
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="oauth_state_mismatch",
            message="OAuth state mismatch — please restart sign-in.",
            retryable=False,
        ) from exc
    except (TokenExchangeError, IdentityVerificationError) as exc:
        _clear_flow_cookies(response)
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="oauth_failed",
            message="Sign-in could not be completed.",
            retryable=True,
        ) from exc
    except ProviderAlreadyLinkedError as exc:
        _clear_flow_cookies(response)
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="provider_already_linked",
            message="That provider identity is already linked to another account.",
            retryable=False,
        ) from exc

    _clear_flow_cookies(response)
    _, refresh_token = await session_store.create(
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    _issue_session_cookies(
        response, state.settings, user_id=user.id, refresh_token=refresh_token
    )
    return MeResponse(user=user, entitlements=_entitlements(user))


@router.post("/auth/refresh")
async def refresh(
    request: Request,
    response: Response,
    state: AppState = Depends(get_state),
    session_store: SessionStore = Depends(_session_store),
) -> dict[str, bool]:
    """Rotate the refresh token and mint a fresh access token. A reused (rotated-out) token is a
    theft signal — the whole session family is revoked and the caller must sign in again."""
    raw_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if not raw_token:
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="no_refresh_token",
            message="No refresh token present.",
            retryable=False,
        )
    user_id = await session_store.user_id_for(raw_token=raw_token)
    try:
        _, new_refresh = await session_store.rotate(raw_token=raw_token)
    except RefreshTokenReuseError as exc:
        _clear_session_cookies(response)
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="refresh_token_reuse",
            message="Session revoked — please sign in again.",
            retryable=False,
        ) from exc
    except RefreshTokenInvalidError as exc:
        _clear_session_cookies(response)
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="refresh_token_invalid",
            message="Refresh token invalid or expired.",
            retryable=False,
        ) from exc

    if user_id is None:  # pragma: no cover — rotate() would already have raised on an unknown token
        _clear_session_cookies(response)
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="reauth_required",
            message="Please sign in again.",
            retryable=False,
        )
    _issue_session_cookies(response, state.settings, user_id=user_id, refresh_token=new_refresh)
    return {"refreshed": True}


@router.post("/auth/logout")
async def logout(
    request: Request,
    response: Response,
    session_store: SessionStore = Depends(_session_store),
) -> dict[str, bool]:
    """Revoke the current refresh session and clear the auth cookies."""
    raw_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if raw_token:
        await session_store.revoke(raw_token=raw_token)
    _clear_session_cookies(response)
    return {"logged_out": True}


@router.get("/me")
async def me(user: User = Depends(require_user)) -> MeResponse:
    return MeResponse(user=user, entitlements=_entitlements(user))


@router.get("/me/connections")
async def connections(
    user: User = Depends(require_user),
    store: UserStore = Depends(get_user_store),
) -> dict[str, list[OAuthAccount]]:
    return {"connections": await store.list_providers(user_id=user.id)}


@router.delete("/me/connections/{provider}")
async def unlink_connection(
    provider: str,
    user: User = Depends(require_user),
    store: UserStore = Depends(get_user_store),
) -> dict[str, bool]:
    """Unlink a provider — refused if it is the account's only remaining login method."""
    oauth_provider = _parse_provider(provider)
    try:
        await store.unlink_provider(user_id=user.id, provider=oauth_provider)
    except LastLoginMethodError as exc:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="last_login_method",
            message="Cannot unlink the only remaining login method.",
            retryable=False,
        ) from exc
    return {"unlinked": True}


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_TOKEN_COOKIE)
    response.delete_cookie(REFRESH_TOKEN_COOKIE)


def _entitlements(user: User) -> EntitlementView:
    # BYOK state isn't wired into this router yet (BYOK key management is a later slice); the
    # entitlement view is computed with byok_active=False until then, which never over-grants.
    return entitlement_view(user, byok_active=False)


__all__ = ["REFRESH_TOKEN_COOKIE", "router"]
