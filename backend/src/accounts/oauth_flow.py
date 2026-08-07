"""Stateless orchestration of the OAuth authorization-code flow, sitting between a future API
router and the provider clients + user store.

`start_authorization` generates the per-request CSRF `state` (plus a PKCE `code_verifier`/
`code_challenge` and a `nonce` for LINE), and returns both the redirect URL and the values the
caller must stash server-side/in a short-lived cookie to check on the callback.

`complete_authorization` verifies `state` byte-exact, exchanges the code, verifies the ID token
(and, for LINE, the `nonce`), then resolves the identity through the store — either as a normal
login/signup or, when a `current_user_id` is supplied, as an explicit "link this provider to the
already-signed-in user" action.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

import structlog
from pydantic import BaseModel, ConfigDict

from accounts.models import OAuthProvider, User
from accounts.oauth_providers import (
    IdentityVerificationError,
    OAuthProviderClient,
    VerifiedIdentity,
    build_provider_client,
)
from accounts.store import UserStore
from config.settings import Settings

logger = structlog.get_logger(__name__)

_CODE_VERIFIER_BYTES = 64  # ~86 url-safe chars, within PKCE's 43-128 range


class StateMismatchError(RuntimeError):
    """The `state` returned on the callback did not match what was issued — a CSRF signal; the
    callback is rejected without any token exchange."""


class AuthorizationRequest(BaseModel):
    """What `start_authorization` hands back: the URL to redirect the user to, plus the values
    the caller must persist for the callback to verify against. `code_verifier` and `nonce` are
    only present for providers that use PKCE/OIDC nonce (LINE)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: OAuthProvider
    authorization_url: str
    state: str
    code_verifier: str | None = None
    nonce: str | None = None


def _generate_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(_CODE_VERIFIER_BYTES)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def start_authorization(
    provider: OAuthProvider,
    settings: Settings,
    *,
    client: OAuthProviderClient | None = None,
) -> AuthorizationRequest:
    client = client or build_provider_client(provider, settings)
    state = secrets.token_urlsafe(32)

    code_verifier: str | None = None
    code_challenge: str | None = None
    nonce: str | None = None
    if client.requires_pkce:
        code_verifier, code_challenge = _generate_pkce_pair()
        nonce = secrets.token_urlsafe(32)

    url = client.build_authorization_url(state=state, code_challenge=code_challenge, nonce=nonce)
    logger.info("oauth_authorization_started", provider=provider.value)
    return AuthorizationRequest(
        provider=provider,
        authorization_url=url,
        state=state,
        code_verifier=code_verifier,
        nonce=nonce,
    )


def line_retry_authorization_url(authorization_url: str) -> str:
    """Build the fallback authorize URL for LINE's in-app auto-login failure (private browsing,
    OS Universal-Link limits): the same URL with `disable_auto_login=true` appended. The caller
    navigates to it via a real link/redirect — a JS-only redirect can defeat LINE's auto-login
    detection. Idempotent if the flag is already present."""
    if "disable_auto_login=true" in authorization_url:
        return authorization_url
    separator = "&" if "?" in authorization_url else "?"
    return f"{authorization_url}{separator}disable_auto_login=true"


async def complete_authorization(  # noqa: PLR0913 — callback carries the request + all stored verifiers
    provider: OAuthProvider,
    code: str,
    state: str,
    stored_state: str,
    *,
    store: UserStore,
    settings: Settings,
    stored_verifier: str | None = None,
    stored_nonce: str | None = None,
    current_user_id: str | None = None,
    client: OAuthProviderClient | None = None,
) -> User:
    if not (stored_state and hmac.compare_digest(state, stored_state)):
        logger.warning("oauth_state_mismatch", provider=provider.value)
        raise StateMismatchError("oauth state mismatch")

    client = client or build_provider_client(provider, settings)
    tokens = await client.exchange_code(code=code, code_verifier=stored_verifier)
    if tokens.id_token is None:
        raise IdentityVerificationError("provider returned no id token")
    identity = await client.verify_id_token(tokens.id_token, nonce=stored_nonce)

    if current_user_id is not None:
        return await _link_to_current_user(store, identity, current_user_id)

    user = await store.upsert_via_oauth(
        provider=identity.provider,
        provider_account_id=identity.provider_account_id,
        email=identity.email,
        email_verified=identity.email_verified,
        display_name=identity.name,
        picture_url=identity.picture,
    )
    logger.info(
        "oauth_authorization_completed", provider=provider.value, user_id=user.id, linked=False
    )
    return user


async def _link_to_current_user(
    store: UserStore, identity: VerifiedIdentity, current_user_id: str
) -> User:
    await store.link_provider(
        user_id=current_user_id,
        provider=identity.provider,
        provider_account_id=identity.provider_account_id,
        email_at_provider=identity.email,
    )
    user = await store.get(user_id=current_user_id)
    logger.info(
        "oauth_authorization_completed",
        provider=identity.provider.value,
        user_id=user.id,
        linked=True,
    )
    return user


__all__ = [
    "AuthorizationRequest",
    "StateMismatchError",
    "complete_authorization",
    "line_retry_authorization_url",
    "start_authorization",
]
