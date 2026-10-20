"""Provider-specific OAuth clients (Google + LINE + Telegram) behind a shared Protocol.

Each provider knows how to build its authorization URL, exchange an authorization code for
tokens at its token endpoint, and turn the resulting ID token into a verified identity. The
router-layer code stays provider-agnostic by depending only on `OAuthProviderClient`.

The provider API endpoints are fixed protocol constants (not secrets); client ids/secrets and
redirect URIs come from config and are never hardcoded or logged. ID-token verification is done
with a real JWT/JWKS library for Google (RS256 against Google's published keys) and with LINE's
own server-side `/verify` endpoint for LINE — neither hand-rolls signature crypto.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlencode

import anyio.to_thread
import httpx
import jwt
import structlog
from jwt import PyJWKClient
from pydantic import BaseModel, ConfigDict

from accounts.models import OAuthProvider
from config.settings import Settings, reveal
from netclient import lease_client

logger = structlog.get_logger(__name__)

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 — fixed protocol URL; not a secret
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
GOOGLE_SCOPES = "openid email profile"

LINE_AUTHORIZATION_ENDPOINT = "https://access.line.me/oauth2/v2.1/authorize"
LINE_TOKEN_ENDPOINT = "https://api.line.me/oauth2/v2.1/token"  # noqa: S105 — fixed protocol URL; not a secret
LINE_VERIFY_ENDPOINT = "https://api.line.me/oauth2/v2.1/verify"
LINE_ISSUER = "https://access.line.me"
LINE_SCOPES = "profile openid email"

TELEGRAM_AUTHORIZATION_ENDPOINT = "https://oauth.telegram.org/auth"
TELEGRAM_TOKEN_ENDPOINT = "https://oauth.telegram.org/token"  # noqa: S105 — fixed protocol URL; not a secret
TELEGRAM_JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
TELEGRAM_ISSUER = "https://oauth.telegram.org"
TELEGRAM_SCOPES = "openid profile"  # no `phone`: Miru never asks for the number

_HTTP_TIMEOUT_SECONDS = 10.0
# PyJWT's `leeway` for iat/exp/nbf checks — absorbs ordinary clock drift between this host and
# the provider's without weakening signature/audience/issuer verification (those are unaffected
# by leeway). 60s comfortably covers typical NTP drift and Docker-Desktop-WSL2 clock lag.
_CLOCK_SKEW_LEEWAY_SECONDS = 60


class OAuthConfigError(RuntimeError):
    """A provider is missing required credentials in config — refuses to start the flow rather
    than fall back to a default."""


class TokenExchangeError(RuntimeError):
    """The provider's token endpoint rejected the code exchange or returned no usable tokens."""


class IdentityVerificationError(RuntimeError):
    """The ID token failed verification (bad signature, wrong issuer/audience, nonce mismatch, or
    expiry) — the callback must not trust it."""


class ProviderTokenResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    access_token: str
    id_token: str | None = None
    refresh_token: str | None = None
    expires_in: int | None = None
    scope: str | None = None
    token_type: str | None = None


class VerifiedIdentity(BaseModel):
    """A provider identity that has passed signature + claim verification. `email` is nullable
    because LINE may return no email (permission declined or not granted); identity is keyed on
    `(provider, provider_account_id)`, never on email."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: OAuthProvider
    provider_account_id: str
    email: str | None = None
    email_verified: bool = False
    name: str = ""
    picture: str | None = None


@runtime_checkable
class OAuthProviderClient(Protocol):
    provider: OAuthProvider
    requires_pkce: bool

    def build_authorization_url(
        self,
        *,
        state: str,
        code_challenge: str | None = None,
        nonce: str | None = None,
    ) -> str: ...

    async def exchange_code(
        self, *, code: str, code_verifier: str | None = None
    ) -> ProviderTokenResponse: ...

    async def verify_id_token(
        self, id_token: str, *, nonce: str | None = None
    ) -> VerifiedIdentity: ...


HttpClientFactory = Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]]


def _default_client_factory() -> AbstractAsyncContextManager[httpx.AsyncClient]:
    # Strict TLS verification (httpx default) — this auth flow never downgrades it. Pooled; no
    # retries (a token exchange is single-use).
    return lease_client(timeout=_HTTP_TIMEOUT_SECONDS)


async def _post_form(
    client_factory: HttpClientFactory,
    url: str,
    data: dict[str, str],
    *,
    auth: tuple[str, str] | None = None,
) -> dict[str, Any]:
    async with client_factory() as client:
        if auth is None:
            response = await client.post(url, data=data)
        else:
            response = await client.post(url, data=data, auth=httpx.BasicAuth(*auth))
    if response.status_code != int(httpx.codes.OK):
        # Never log `data` (carries the client secret / code / verifier) — only the provider's
        # own error body, which is documented to be error-code-shaped, not credential-shaped.
        logger.warning(
            "oauth_provider_http_error",
            url=url,
            status_code=response.status_code,
            body=response.text[:500],
        )
        raise TokenExchangeError(f"provider returned HTTP {response.status_code}")
    payload: dict[str, Any] = response.json()
    return payload


class SigningKeyClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...  # noqa: ANN401 - PyJWT returns an untyped PyJWK


class GoogleOAuthProvider:
    provider = OAuthProvider.GOOGLE
    requires_pkce = False

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: HttpClientFactory = _default_client_factory,
        signing_key_client: SigningKeyClient | None = None,
    ) -> None:
        if not (settings.google_client_id and settings.google_client_secret):
            raise OAuthConfigError("google client id/secret not configured")
        if not settings.google_redirect_uri:
            raise OAuthConfigError("google redirect uri not configured")
        self._client_id = settings.google_client_id
        self._client_secret = settings.google_client_secret.get_secret_value()
        self._redirect_uri = settings.google_redirect_uri
        self._client_factory = client_factory
        self._signing_key_client = signing_key_client

    def build_authorization_url(
        self,
        *,
        state: str,
        code_challenge: str | None = None,
        nonce: str | None = None,
    ) -> str:
        del code_challenge, nonce  # accepted for the Protocol; Google's flow here uses neither
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_SCOPES,
            "state": state,
            "access_type": "offline",
            "include_granted_scopes": "true",
        }
        return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(
        self, *, code: str, code_verifier: str | None = None
    ) -> ProviderTokenResponse:
        data = {
            "code": code,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "redirect_uri": self._redirect_uri,
            "grant_type": "authorization_code",
        }
        payload = await _post_form(self._client_factory, GOOGLE_TOKEN_ENDPOINT, data)
        return ProviderTokenResponse.model_validate(payload)

    def _resolve_key(self, id_token: str) -> Any:  # noqa: ANN401 - PyJWT key union (RSA/EC/OKP)
        client = self._signing_key_client or PyJWKClient(GOOGLE_CERTS_URL)
        return client.get_signing_key_from_jwt(id_token).key

    async def verify_id_token(self, id_token: str, *, nonce: str | None = None) -> VerifiedIdentity:
        # JWKS resolution and RS256 verification are blocking (urllib fetch + crypto); keep them
        # off the event loop.
        signing_key: Any = await anyio.to_thread.run_sync(self._resolve_key, id_token)
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                signing_key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=list(GOOGLE_ISSUERS),
                # Tolerate small clock skew between this host and Google's — without it, `iat`/
                # `exp`/`nbf` are checked against wall-clock-exact, and container clocks (esp.
                # under Docker Desktop's WSL2 VM after a host sleep/resume) can drift by a few
                # seconds, rejecting an otherwise-valid token as "not yet valid" (iat).
                leeway=_CLOCK_SKEW_LEEWAY_SECONDS,
            )
        except jwt.PyJWTError as exc:
            # The specific PyJWT exception class (ExpiredSignatureError, InvalidAudienceError,
            # InvalidIssuerError, ...) is the actual diagnostic — surfaced here since the router's
            # catch-all log site only sees the wrapping IdentityVerificationError's static message.
            logger.warning(
                "google_id_token_verification_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise IdentityVerificationError("google id token verification failed") from exc

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise IdentityVerificationError("google id token missing subject")
        identity = VerifiedIdentity(
            provider=OAuthProvider.GOOGLE,
            provider_account_id=subject,
            email=claims.get("email"),
            email_verified=bool(claims.get("email_verified", False)),
            name=str(claims.get("name", "")),
            picture=claims.get("picture"),
        )
        logger.info(
            "oauth_id_token_verified",
            provider=OAuthProvider.GOOGLE.value,
            provider_account_id=identity.provider_account_id,
        )
        return identity


class LineOAuthProvider:
    provider = OAuthProvider.LINE
    requires_pkce = True

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: HttpClientFactory = _default_client_factory,
    ) -> None:
        if not (settings.line_channel_id and settings.line_channel_secret):
            raise OAuthConfigError("line channel id/secret not configured")
        if not settings.line_redirect_uri:
            raise OAuthConfigError("line redirect uri not configured")
        self._channel_id = settings.line_channel_id
        self._channel_secret = settings.line_channel_secret.get_secret_value()
        self._redirect_uri = settings.line_redirect_uri
        self._client_factory = client_factory

    def build_authorization_url(
        self,
        *,
        state: str,
        code_challenge: str | None = None,
        nonce: str | None = None,
    ) -> str:
        if not code_challenge:
            raise OAuthConfigError("line authorization requires a PKCE code challenge")
        if not nonce:
            raise OAuthConfigError("line authorization requires a nonce")
        params = {
            "response_type": "code",
            "client_id": self._channel_id,
            "redirect_uri": self._redirect_uri,
            "state": state,
            "scope": LINE_SCOPES,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
        }
        return f"{LINE_AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(
        self, *, code: str, code_verifier: str | None = None
    ) -> ProviderTokenResponse:
        if not code_verifier:
            raise TokenExchangeError("line code exchange requires the PKCE code verifier")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "client_id": self._channel_id,
            "client_secret": self._channel_secret,
            "code_verifier": code_verifier,
        }
        payload = await _post_form(self._client_factory, LINE_TOKEN_ENDPOINT, data)
        return ProviderTokenResponse.model_validate(payload)

    async def verify_id_token(self, id_token: str, *, nonce: str | None = None) -> VerifiedIdentity:
        # LINE validates the ID token server-side and returns its claims — no local HMAC-secret
        # handling. The web-login ID token is HS256-signed with the channel secret, so keeping
        # that secret out of a JWT-verification path is deliberate hygiene.
        data = {"id_token": id_token, "client_id": self._channel_id}
        claims = await _post_form(self._client_factory, LINE_VERIFY_ENDPOINT, data)

        if claims.get("iss") != LINE_ISSUER:
            raise IdentityVerificationError("line id token has an unexpected issuer")
        if claims.get("aud") != self._channel_id:
            raise IdentityVerificationError("line id token audience mismatch")
        if nonce is not None and claims.get("nonce") != nonce:
            raise IdentityVerificationError("line id token nonce mismatch")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise IdentityVerificationError("line id token missing subject")
        # LINE's /verify only returns a claim it has itself validated; a present email from LINE
        # is treated as verified.
        email = claims.get("email")
        identity = VerifiedIdentity(
            provider=OAuthProvider.LINE,
            provider_account_id=subject,
            email=email,
            email_verified=email is not None,
            name=str(claims.get("name", "")),
            picture=claims.get("picture"),
        )
        logger.info(
            "oauth_id_token_verified",
            provider=OAuthProvider.LINE.value,
            provider_account_id=identity.provider_account_id,
        )
        return identity


def telegram_client_id(settings: Settings) -> str | None:
    """The Login client id: explicit, else the bot's numeric id (the part of the bot token before
    the colon)."""
    if settings.telegram_login_client_id:
        return settings.telegram_login_client_id
    token = reveal(settings.telegram_bot_token)
    return token.partition(":")[0] if token and ":" in token else None


class TelegramOAuthProvider:
    """Log in with Telegram — OpenID Connect (code + PKCE), core.telegram.org/bots/telegram-login.

    The token endpoint takes the client id/secret as HTTP Basic auth; the ID token is RS256-signed
    and verified against Telegram's JWKS. There is no email: `sub` (the Telegram user id) is the
    identity."""

    provider = OAuthProvider.TELEGRAM
    requires_pkce = True

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: HttpClientFactory = _default_client_factory,
        signing_key_client: SigningKeyClient | None = None,
    ) -> None:
        client_id = telegram_client_id(settings)
        if not (client_id and settings.telegram_login_client_secret):
            raise OAuthConfigError("telegram login client id/secret not configured")
        if not settings.telegram_login_redirect_uri:
            raise OAuthConfigError("telegram login redirect uri not configured")
        self._client_id = client_id
        self._client_secret = settings.telegram_login_client_secret.get_secret_value()
        self._redirect_uri = settings.telegram_login_redirect_uri
        self._client_factory = client_factory
        self._signing_key_client = signing_key_client

    def build_authorization_url(
        self,
        *,
        state: str,
        code_challenge: str | None = None,
        nonce: str | None = None,
    ) -> str:
        if not code_challenge:
            raise OAuthConfigError("telegram authorization requires a PKCE code challenge")
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": TELEGRAM_SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if nonce:
            params["nonce"] = nonce
        return f"{TELEGRAM_AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(
        self, *, code: str, code_verifier: str | None = None
    ) -> ProviderTokenResponse:
        if not code_verifier:
            raise TokenExchangeError("telegram code exchange requires the PKCE code verifier")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "client_id": self._client_id,
            "code_verifier": code_verifier,
        }
        payload = await _post_form(
            self._client_factory,
            TELEGRAM_TOKEN_ENDPOINT,
            data,
            auth=(self._client_id, self._client_secret),
        )
        return ProviderTokenResponse.model_validate(payload)

    def _resolve_key(self, id_token: str) -> Any:  # noqa: ANN401 - PyJWT key union (RSA/EC/OKP)
        client = self._signing_key_client or PyJWKClient(TELEGRAM_JWKS_URL)
        return client.get_signing_key_from_jwt(id_token).key

    async def verify_id_token(self, id_token: str, *, nonce: str | None = None) -> VerifiedIdentity:
        signing_key: Any = await anyio.to_thread.run_sync(self._resolve_key, id_token)
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                signing_key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=TELEGRAM_ISSUER,
                leeway=_CLOCK_SKEW_LEEWAY_SECONDS,
            )
        except jwt.PyJWTError as exc:
            logger.warning(
                "telegram_id_token_verification_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise IdentityVerificationError("telegram id token verification failed") from exc
        if "nonce" in claims and nonce is not None and claims["nonce"] != nonce:
            raise IdentityVerificationError("telegram id token nonce mismatch")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise IdentityVerificationError("telegram id token missing subject")
        identity = VerifiedIdentity(
            provider=OAuthProvider.TELEGRAM,
            provider_account_id=subject,
            name=str(claims.get("name") or claims.get("preferred_username") or ""),
            picture=claims.get("picture"),
        )
        logger.info(
            "oauth_id_token_verified",
            provider=OAuthProvider.TELEGRAM.value,
            provider_account_id=identity.provider_account_id,
        )
        return identity


def build_provider_client(
    provider: OAuthProvider,
    settings: Settings,
    *,
    client_factory: HttpClientFactory = _default_client_factory,
) -> OAuthProviderClient:
    if provider is OAuthProvider.GOOGLE:
        return GoogleOAuthProvider(settings, client_factory=client_factory)
    if provider is OAuthProvider.TELEGRAM:
        return TelegramOAuthProvider(settings, client_factory=client_factory)
    return LineOAuthProvider(settings, client_factory=client_factory)


__all__ = [
    "GoogleOAuthProvider",
    "IdentityVerificationError",
    "LineOAuthProvider",
    "OAuthConfigError",
    "OAuthProviderClient",
    "ProviderTokenResponse",
    "TelegramOAuthProvider",
    "TokenExchangeError",
    "VerifiedIdentity",
    "build_provider_client",
    "telegram_client_id",
]
