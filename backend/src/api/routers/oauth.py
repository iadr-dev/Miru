"""The authorization server MCP clients sign in through ("sign in with your Miru account").

Two routers:

`router` - the protocol surface, mounted at the ROOT of the API host because the endpoints are
named by the issuer metadata (RFC 8414) and a client looks the metadata up at a fixed well-known
path:

    GET  /.well-known/oauth-authorization-server   issuer metadata
    GET  /oauth/authorize                          validates the request, sends the browser to
                                                   the web app's consent screen
    POST /oauth/token                              code -> tokens, refresh (rotating)
    POST /oauth/register                           RFC 7591 dynamic client registration
    POST /oauth/revoke                             RFC 7009

`web_router` - JSON for the web app, under /api/v1 and authenticated by the user's session cookie:

    GET    /oauth/consent/{request_id}             what the user is being asked to approve
    POST   /oauth/consent/{request_id}             approve / deny -> where to send the browser

Protocol endpoints answer in RFC 6749 JSON (`{"error", "error_description"}`), not the app's own
error envelope, because the clients parse those. Tokens, codes, verifiers and refresh tokens are
never logged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlencode

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.mcp_oauth.service import (
    MAX_CLIENT_NAME_LENGTH,
    AuthorizationServer,
    AuthorizeRejectedError,
    ClientMetadataSource,
    ConsentError,
    IssuedTokens,
    OAuthProtocolError,
    build_redirect,
)
from accounts.mcp_tokens import MCP_SCOPE_READ, SUPPORTED_SCOPES
from accounts.models import User
from api.dependencies import (
    AppState,
    get_session,
    get_settings_dep,
    get_state,
    require_user,
    system_scope,
)
from api.errors import ApiError
from api.rate_guard import client_address, guard_auth, rate_limit_address
from config.settings import Settings
from dataaccess.oauth_client_metadata import build_client_metadata_fetcher

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["oauth"])
web_router = APIRouter(tags=["oauth"])

_MAX_FORM_BYTES = 16_384
_REGISTER_WINDOW_SECONDS = 3_600
_TOKEN_WINDOW_SECONDS = 60
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
_GRANT_TYPES = ("authorization_code", "refresh_token")


# ---------------------------------------------------------------------------------- models


class AuthorizationServerMetadata(BaseModel):
    """RFC 8414 authorization server metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    revocation_endpoint: str
    response_types_supported: list[Literal["code"]]
    grant_types_supported: list[Literal["authorization_code", "refresh_token"]]
    code_challenge_methods_supported: list[Literal["S256"]]
    token_endpoint_auth_methods_supported: list[Literal["none"]]
    revocation_endpoint_auth_methods_supported: list[Literal["none"]]
    scopes_supported: list[str]
    client_id_metadata_document_supported: bool
    authorization_response_iss_parameter_supported: bool
    service_documentation: str


class OAuthErrorBody(BaseModel):
    """RFC 6749 section 5.2 error response."""

    error: str
    error_description: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    refresh_token: str
    scope: str


class RegisterRequest(BaseModel):
    """RFC 7591 client metadata. Unknown members are ignored, as the RFC allows."""

    model_config = ConfigDict(extra="ignore")

    client_name: str | None = Field(default=None, max_length=200)
    redirect_uris: list[str] = Field(default_factory=list)
    token_endpoint_auth_method: str | None = None
    grant_types: list[str] | None = None
    response_types: list[str] | None = None
    scope: str | None = Field(default=None, max_length=500)


class RegisterResponse(BaseModel):
    client_id: str
    client_id_issued_at: int
    client_name: str
    redirect_uris: list[str]
    token_endpoint_auth_method: Literal["none"] = "none"
    grant_types: list[str]
    response_types: list[Literal["code"]]
    scope: str


class ConsentClient(BaseModel):
    name: str
    client_id: str
    # `registered` = registered by the client itself (anyone can pick any name); `metadata_url`
    # = the client_id is an https URL the server fetched - show the URL host as its identity.
    kind: Literal["registered", "metadata_url"]
    # The host of the redirect URI the decision will send the browser to.
    redirect_host: str
    # true only for client_ids the operator lists in OAUTH_VERIFIED_CLIENT_IDS. Anyone can
    # register under any name, so show an "unverified app" warning when this is false.
    verified: bool


class ConsentScope(BaseModel):
    id: str
    description: str


class ConsentResource(BaseModel):
    url: str
    label: str


class ConsentResponse(BaseModel):
    request_id: str
    client: ConsentClient
    # The exact URL the browser is sent to on approve / deny (from the stored request).
    redirect_uri: str
    scopes: list[ConsentScope]
    resource: ConsentResource
    nonce: str
    expires_at: datetime


class ConsentDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "deny"]
    nonce: str = Field(min_length=1, max_length=200)


class ConsentDecisionResponse(BaseModel):
    # Where to send the browser: the client's redirect URI with `code`/`state`/`iss` (approve)
    # or `error=access_denied` (deny).
    redirect_to: str


class RevokeTokenResponse(BaseModel):
    """RFC 7009 answers with an empty JSON object."""

    model_config = ConfigDict(extra="forbid")


_SCOPE_DESCRIPTIONS = {
    MCP_SCOPE_READ: "Read Miru market data and analysis as you, within your plan.",
}


# ---------------------------------------------------------------------------------- plumbing


def get_metadata_source(state: Annotated[AppState, Depends(get_state)]) -> ClientMetadataSource:
    """Seam for the Client ID Metadata Document fetcher (overridden in tests)."""
    return build_client_metadata_fetcher(state.settings)


def get_authorization_server(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    state: Annotated[AppState, Depends(get_state)],
    metadata_source: Annotated[ClientMetadataSource, Depends(get_metadata_source)],
) -> AuthorizationServer:
    async def may_fetch() -> bool:
        # A NEW metadata document makes this server open a connection of its own, so it is
        # metered far tighter than the endpoint itself.
        address = client_address(request, trusted_proxy_count=state.settings.trusted_proxy_count)
        verdict = await state.rate_limiter.check(
            f"oauth:cimd:ip:{rate_limit_address(address)}",
            max_requests=state.settings.oauth_cimd_fetch_per_minute,
            window_seconds=_TOKEN_WINDOW_SECONDS,
        )
        return verdict.allowed

    return AuthorizationServer(
        session, state.settings, metadata_source=metadata_source, fetch_gate=may_fetch
    )


def _error(error: str, description: str, *, status_code: int = 400) -> JSONResponse:
    body = OAuthErrorBody(error=error, error_description=description)
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        headers=_NO_STORE,
    )


def _protocol_error(exc: OAuthProtocolError) -> JSONResponse:
    return _error(exc.error, exc.description, status_code=exc.status)


async def _throttle(
    request: Request, state: AppState, *, bucket: str, limit: int, window: int
) -> JSONResponse | None:
    address = client_address(request, trusted_proxy_count=state.settings.trusted_proxy_count)
    verdict = await state.rate_limiter.check(
        f"oauth:{bucket}:ip:{rate_limit_address(address)}",
        max_requests=limit,
        window_seconds=window,
    )
    if verdict.allowed:
        return None
    retry = max(1, int(verdict.retry_after_seconds or window))
    logger.warning("oauth_rate_limited", bucket=bucket)
    response = _error("temporarily_unavailable", "Too many requests; slow down.", status_code=429)
    response.headers["Retry-After"] = str(retry)
    return response


async def _read_capped(request: Request) -> bytes | None:
    """The request body, or None if it exceeds the cap. A declared Content-Length over the cap is
    refused before anything is read, and the stream is counted as it arrives and abandoned at the
    cap, so a body that lies about (or omits) its length is never buffered whole."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > _MAX_FORM_BYTES:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_FORM_BYTES:
            return None
    return bytes(body)


async def _form(request: Request) -> dict[str, str] | JSONResponse:
    """The urlencoded body as a flat dict; a repeated parameter is an error (RFC 6749 3.2)."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != _FORM_CONTENT_TYPE:
        return _error("invalid_request", f"Content-Type must be {_FORM_CONTENT_TYPE}.")
    raw = await _read_capped(request)
    if raw is None:
        return _error("invalid_request", "Request body is too large.", status_code=413)
    try:
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=50)
    except ValueError:
        return _error("invalid_request", "Malformed form body.")
    if any(len(values) != 1 for values in parsed.values()):
        return _error("invalid_request", "A parameter was repeated.")
    return {key: values[0] for key, values in parsed.items()}


def _token_response(tokens: IssuedTokens) -> JSONResponse:
    body = TokenResponse(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        refresh_token=tokens.refresh_token,
        scope=tokens.scope,
    )
    return JSONResponse(content=body.model_dump(), headers=_NO_STORE)


def _locale(request: Request) -> str:
    first = request.headers.get("accept-language", "").split(",", 1)[0].strip().lower()
    return "en" if first.startswith("en") else "zh-TW"


def _consent_page(settings: Settings, http_request: Request, /, **query: str) -> str:
    base = settings.frontend_base_url.rstrip("/")
    return f"{base}/{_locale(http_request)}/oauth/consent?{urlencode(query)}"


def _require_configured(settings: Settings) -> JSONResponse | None:
    if settings.oauth_issuer_url() is None or settings.mcp_resource_url() is None:
        return _error(
            "server_error",
            "Sign-in for MCP clients is not configured (OAUTH_ISSUER / MCP_PUBLIC_BASE_URL).",
            status_code=503,
        )
    return None


# ---------------------------------------------------------------------------------- metadata


@router.get(
    "/.well-known/oauth-authorization-server",
    response_model=AuthorizationServerMetadata,
    responses={404: {"model": OAuthErrorBody}},
)
async def authorization_server_metadata(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    issuer = settings.oauth_issuer_url()
    if issuer is None:
        return _error("not_configured", "No authorization server is configured.", status_code=404)
    document = AuthorizationServerMetadata(
        issuer=issuer,
        authorization_endpoint=f"{issuer}/oauth/authorize",
        token_endpoint=f"{issuer}/oauth/token",
        registration_endpoint=f"{issuer}/oauth/register",
        revocation_endpoint=f"{issuer}/oauth/revoke",
        response_types_supported=["code"],
        grant_types_supported=["authorization_code", "refresh_token"],
        code_challenge_methods_supported=["S256"],
        token_endpoint_auth_methods_supported=["none"],
        revocation_endpoint_auth_methods_supported=["none"],
        scopes_supported=list(SUPPORTED_SCOPES),
        client_id_metadata_document_supported=True,
        authorization_response_iss_parameter_supported=True,
        service_documentation="https://modelcontextprotocol.io/specification/",
    )
    return JSONResponse(content=document.model_dump(), headers={"Cache-Control": "max-age=300"})


# ---------------------------------------------------------------------------------- authorize


@router.get(
    "/oauth/authorize",
    dependencies=[Depends(guard_auth), Depends(system_scope)],
    status_code=status.HTTP_302_FOUND,
    response_class=RedirectResponse,
    responses={302: {"description": "To the consent screen, or back to the client with an error."}},
)
async def authorize(
    request: Request,
    server: Annotated[AuthorizationServer, Depends(get_authorization_server)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    client_id: Annotated[str | None, Query(max_length=2048)] = None,
    redirect_uri: Annotated[str | None, Query(max_length=2048)] = None,
    response_type: Annotated[str | None, Query(max_length=32)] = None,
    code_challenge: Annotated[str | None, Query(max_length=256)] = None,
    code_challenge_method: Annotated[str | None, Query(max_length=32)] = None,
    resource: Annotated[str | None, Query(max_length=2048)] = None,
    scope: Annotated[str | None, Query(max_length=500)] = None,
    state_param: Annotated[str | None, Query(alias="state", max_length=4096)] = None,
) -> Response:
    """Validate the request and hand the browser to the web app's consent screen.

    The user's session is not looked at here: the consent screen asks the API for the request
    (`GET /api/v1/oauth/consent/{id}`), and a signed-out user is sent through sign-in and back to
    that same screen. Anything wrong with the client or redirect URI is shown to the user, never
    redirected; anything else goes back to the (verified) redirect URI as an RFC 6749 error."""
    problem = _require_configured(settings)
    if problem is not None:
        return problem
    if client_id is None:
        return RedirectResponse(
            _consent_page(settings, request, error="invalid_request"), status_code=302
        )
    try:
        begun = await server.begin_authorization(
            client_id=client_id,
            redirect_uri=redirect_uri,
            response_type=response_type,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            resource=resource,
            scope=scope,
            state=state_param,
        )
    except AuthorizeRejectedError as rejected:
        logger.info("oauth_authorize_rejected", error=rejected.error)
        if rejected.redirect_uri is None:
            return RedirectResponse(
                _consent_page(settings, request, error=rejected.error), status_code=302
            )
        return RedirectResponse(
            build_redirect(
                rejected.redirect_uri,
                error=rejected.error,
                error_description=rejected.description,
                state=rejected.state,
                iss=server.issuer,
            ),
            status_code=302,
        )
    redirect = RedirectResponse(
        _consent_page(settings, request, request=begun.request_id), status_code=302
    )
    # Ties the consent screen to THIS browser: the secret is only in this cookie, and only its hash
    # is stored. httpOnly + SameSite=Lax, host-only on the API origin, so the web app's consent
    # calls must be made with credentials (the same way the session cookie already is).
    redirect.set_cookie(
        consent_cookie_name(begun.request_id),
        begun.browser_secret,
        httponly=True,
        secure=settings.env == "production",
        samesite="lax",
        max_age=settings.oauth_authorization_request_ttl_seconds,
        path="/",
    )
    return redirect


# ---------------------------------------------------------------------------------- token


_TOKEN_FORM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "grant_type": {"type": "string", "enum": list(_GRANT_TYPES)},
        "code": {"type": "string"},
        "redirect_uri": {"type": "string"},
        "code_verifier": {"type": "string"},
        "refresh_token": {"type": "string"},
        "client_id": {"type": "string"},
        "resource": {"type": "string"},
        "scope": {"type": "string"},
    },
    "required": ["grant_type", "client_id"],
}


@router.post(
    "/oauth/token",
    dependencies=[Depends(system_scope)],
    response_model=TokenResponse,
    responses={400: {"model": OAuthErrorBody}, 429: {"model": OAuthErrorBody}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {_FORM_CONTENT_TYPE: {"schema": _TOKEN_FORM_SCHEMA}},
        }
    },
)
async def token(  # noqa: PLR0911 - one early return per RFC 6749 error
    request: Request,
    state: Annotated[AppState, Depends(get_state)],
    server: Annotated[AuthorizationServer, Depends(get_authorization_server)],
) -> Response:
    """Redeem an authorization code (PKCE) or rotate a refresh token. Public clients only."""
    problem = _require_configured(state.settings)
    if problem is not None:
        return problem
    throttled = await _throttle(
        request,
        state,
        bucket="token",
        limit=state.settings.oauth_token_per_minute,
        window=_TOKEN_WINDOW_SECONDS,
    )
    if throttled is not None:
        return throttled
    form = await _form(request)
    if isinstance(form, JSONResponse):
        return form
    client_id = form.get("client_id")
    if not client_id:
        return _error("invalid_client", "client_id is required (this server has public clients).")
    grant_type = form.get("grant_type")
    try:
        if grant_type == "authorization_code":
            code = form.get("code")
            if not code:
                return _error("invalid_request", "code is required.")
            tokens = await server.exchange_code(
                client_id=client_id,
                code=code,
                redirect_uri=form.get("redirect_uri"),
                code_verifier=form.get("code_verifier"),
                resource=form.get("resource"),
            )
        elif grant_type == "refresh_token":
            refresh_token = form.get("refresh_token")
            if not refresh_token:
                return _error("invalid_request", "refresh_token is required.")
            tokens = await server.refresh(
                client_id=client_id,
                refresh_token=refresh_token,
                scope=form.get("scope"),
                resource=form.get("resource"),
            )
        else:
            return _error(
                "unsupported_grant_type", "grant_type must be authorization_code or refresh_token."
            )
    except OAuthProtocolError as exc:
        return _protocol_error(exc)
    return _token_response(tokens)


# ---------------------------------------------------------------------------------- register


@router.post(
    "/oauth/register",
    dependencies=[Depends(system_scope)],
    status_code=status.HTTP_201_CREATED,
    response_model=RegisterResponse,
    responses={400: {"model": OAuthErrorBody}, 429: {"model": OAuthErrorBody}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": RegisterRequest.model_json_schema()}},
        }
    },
)
async def register(  # noqa: PLR0911 - one early return per RFC 7591 error
    request: Request,
    state: Annotated[AppState, Depends(get_state)],
    server: Annotated[AuthorizationServer, Depends(get_authorization_server)],
) -> Response:
    """RFC 7591 dynamic client registration for public (no-secret) clients."""
    problem = _require_configured(state.settings)
    if problem is not None:
        return problem
    throttled = await _throttle(
        request,
        state,
        bucket="register",
        limit=state.settings.oauth_register_per_hour,
        window=_REGISTER_WINDOW_SECONDS,
    )
    if throttled is not None:
        return throttled
    everyone = await state.rate_limiter.check(
        "oauth:register:global",
        max_requests=state.settings.oauth_register_global_per_hour,
        window_seconds=_REGISTER_WINDOW_SECONDS,
    )
    if not everyone.allowed:
        logger.warning("oauth_rate_limited", bucket="register_global")
        return _error(
            "temporarily_unavailable", "Registration is busy; try later.", status_code=429
        )
    raw = await _read_capped(request)
    if raw is None:
        return _error("invalid_client_metadata", "Request body is too large.", status_code=413)
    try:
        metadata = RegisterRequest.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        return _error(
            "invalid_client_metadata", "The body must be a JSON object of client metadata."
        )
    invalid = _check_registration(metadata)
    if invalid is not None:
        return invalid
    try:
        client = await server.register_client(
            client_name=metadata.client_name, redirect_uris=metadata.redirect_uris
        )
    except OAuthProtocolError as exc:
        return _protocol_error(exc)
    body = RegisterResponse(
        client_id=client.client_id,
        client_id_issued_at=int(datetime.now(UTC).timestamp()),
        client_name=client.name[:MAX_CLIENT_NAME_LENGTH],
        redirect_uris=list(client.redirect_uris),
        grant_types=list(_GRANT_TYPES),
        response_types=["code"],
        scope=MCP_SCOPE_READ,
    )
    return JSONResponse(status_code=201, content=body.model_dump(), headers=_NO_STORE)


def _check_registration(metadata: RegisterRequest) -> JSONResponse | None:
    if metadata.token_endpoint_auth_method not in (None, "none"):
        return _error(
            "invalid_client_metadata", "Only token_endpoint_auth_method=none is supported."
        )
    if metadata.grant_types is not None and not set(metadata.grant_types) <= set(_GRANT_TYPES):
        return _error(
            "invalid_client_metadata", "grant_types must be authorization_code / refresh_token."
        )
    if metadata.response_types is not None and set(metadata.response_types) != {"code"}:
        return _error("invalid_client_metadata", "response_types must be [code].")
    return None


# ---------------------------------------------------------------------------------- revoke


_REVOKE_FORM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "token": {"type": "string"},
        "token_type_hint": {"type": "string"},
        "client_id": {"type": "string"},
    },
    "required": ["token"],
}


@router.post(
    "/oauth/revoke",
    dependencies=[Depends(system_scope)],
    status_code=status.HTTP_200_OK,
    response_model=RevokeTokenResponse,
    responses={400: {"model": OAuthErrorBody}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {_FORM_CONTENT_TYPE: {"schema": _REVOKE_FORM_SCHEMA}},
        }
    },
)
async def revoke(
    request: Request,
    state: Annotated[AppState, Depends(get_state)],
    server: Annotated[AuthorizationServer, Depends(get_authorization_server)],
) -> Response:
    """RFC 7009: ends the grant a refresh or access token belongs to. Answers 200 whether or not
    the token was known, so it cannot be used to probe for valid tokens."""
    problem = _require_configured(state.settings)
    if problem is not None:
        return problem
    throttled = await _throttle(
        request,
        state,
        bucket="token",
        limit=state.settings.oauth_token_per_minute,
        window=_TOKEN_WINDOW_SECONDS,
    )
    if throttled is not None:
        return throttled
    form = await _form(request)
    if isinstance(form, JSONResponse):
        return form
    token_value = form.get("token")
    if not token_value:
        return _error("invalid_request", "token is required.")
    await server.revoke_token(token=token_value, client_id=form.get("client_id") or None)
    return JSONResponse(content={}, headers=_NO_STORE)


# ---------------------------------------------------------------- web app: consent


def consent_cookie_name(request_id: str) -> str:
    return f"oauth_req_{request_id}"


def _consent_error(exc: ConsentError) -> ApiError:
    if exc.failure in {"browser_mismatch", "user_mismatch"}:
        return ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code=f"oauth_{exc.failure}",
            message=(
                "This sign-in was started in another browser or by another account. "
                "Start it again from the app, in this browser."
            ),
            retryable=False,
        )
    if exc.failure == "nonce_mismatch":
        return ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="oauth_nonce_mismatch",
            message="This approval did not come from the consent screen. Start sign-in again.",
            retryable=False,
        )
    return ApiError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="oauth_request_not_found",
        message="This sign-in request has expired or was already answered. Start it again.",
        retryable=False,
    )


@web_router.get("/oauth/consent/{request_id}")
async def get_consent(
    request_id: str,
    request: Request,
    user: Annotated[User, Depends(require_user)],
    server: Annotated[AuthorizationServer, Depends(get_authorization_server)],
) -> ConsentResponse:
    """What the signed-in user is being asked to approve. The `nonce` must be echoed in the
    decision: only this response contains it."""
    try:
        view = await server.consent_view(
            request_id,
            user_id=user.id,
            browser_secret=request.cookies.get(consent_cookie_name(request_id)),
        )
    except ConsentError as exc:
        raise _consent_error(exc) from exc
    return ConsentResponse(
        request_id=view.request_id,
        client=ConsentClient(
            name=view.client_name,
            client_id=view.client_id,
            kind="metadata_url" if view.client_kind == "cimd" else "registered",
            redirect_host=view.redirect_host,
            verified=view.verified,
        ),
        redirect_uri=view.redirect_uri,
        scopes=[
            ConsentScope(id=s, description=_SCOPE_DESCRIPTIONS.get(s, s))
            for s in view.scope.split()
        ],
        resource=ConsentResource(url=view.resource, label="Miru MCP"),
        nonce=view.nonce,
        expires_at=view.expires_at,
    )


@web_router.post("/oauth/consent/{request_id}", dependencies=[Depends(guard_auth)])
async def decide_consent(
    request_id: str,
    body: ConsentDecisionRequest,
    request: Request,
    user: Annotated[User, Depends(require_user)],
    server: Annotated[AuthorizationServer, Depends(get_authorization_server)],
) -> ConsentDecisionResponse:
    """Approve or deny. CSRF: the call needs the session cookie, a JSON body (a cross-site form
    cannot send one) and the per-request nonce that only the consent GET reveals."""
    try:
        redirect_to = await server.decide(
            request_id=request_id,
            user_id=user.id,
            approve=body.decision == "approve",
            nonce=body.nonce,
            browser_secret=request.cookies.get(consent_cookie_name(request_id)),
        )
    except ConsentError as exc:
        raise _consent_error(exc) from exc
    except OAuthProtocolError as exc:
        raise ApiError(
            status_code=exc.status, code=exc.error, message=exc.description, retryable=False
        ) from exc
    return ConsentDecisionResponse(redirect_to=redirect_to)


__all__ = ["get_metadata_source", "router", "web_router"]
