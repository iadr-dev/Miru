"""OAuth authorization-code flow orchestration: URL construction, CSRF/nonce defense, code
exchange + identity resolution, the link-to-current-user path, and the LINE auto-login retry.

All provider I/O is stubbed — the default suite makes no live calls to Google or LINE."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from accounts.models import OAuthProvider
from accounts.oauth_flow import (
    StateMismatchError,
    complete_authorization,
    line_retry_authorization_url,
    start_authorization,
)
from accounts.oauth_providers import (
    IdentityVerificationError,
    ProviderTokenResponse,
    VerifiedIdentity,
)
from accounts.store import SqlAlchemyUserStore
from config.settings import Settings


def _settings() -> Settings:
    return Settings(
        google_client_id="g-client",
        google_client_secret="g-secret",
        google_redirect_uri="https://app.example/auth/oauth/google/callback",
        line_channel_id="line-channel",
        line_channel_secret="line-secret",
        line_redirect_uri="https://app.example/auth/oauth/line/callback",
    )


class _StubProvider:
    def __init__(
        self,
        provider: OAuthProvider,
        *,
        requires_pkce: bool,
        identity: VerifiedIdentity,
    ) -> None:
        self.provider = provider
        self.requires_pkce = requires_pkce
        self._identity = identity
        self.exchanged_verifier: str | None = None
        self.verified_nonce: str | None = None

    def build_authorization_url(
        self,
        *,
        state: str,
        code_challenge: str | None = None,
        nonce: str | None = None,
    ) -> str:
        params = [f"state={state}"]
        if code_challenge:
            params.append(f"code_challenge={code_challenge}&code_challenge_method=S256")
        if nonce:
            params.append(f"nonce={nonce}")
        return "https://provider.example/authorize?" + "&".join(params)

    async def exchange_code(
        self, *, code: str, code_verifier: str | None = None
    ) -> ProviderTokenResponse:
        self.exchanged_verifier = code_verifier
        return ProviderTokenResponse(access_token="at", id_token="idt")

    async def verify_id_token(self, id_token: str, *, nonce: str | None = None) -> VerifiedIdentity:
        self.verified_nonce = nonce
        return self._identity


def _google_identity() -> VerifiedIdentity:
    return VerifiedIdentity(
        provider=OAuthProvider.GOOGLE,
        provider_account_id="g-sub-1",
        email="a@x.com",
        email_verified=True,
        name="Ann",
        picture="https://img/a.png",
    )


async def test_google_authorization_url_has_state_and_no_pkce() -> None:
    req = start_authorization(OAuthProvider.GOOGLE, _settings())
    parsed = urlparse(req.authorization_url)
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["state"] == [req.state]
    assert "code_challenge" not in qs
    assert req.code_verifier is None
    assert req.nonce is None


async def test_line_authorization_url_has_pkce_and_nonce() -> None:
    req = start_authorization(OAuthProvider.LINE, _settings())
    parsed = urlparse(req.authorization_url)
    qs = parse_qs(parsed.query)
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["code_challenge"][0]
    assert req.code_verifier is not None
    assert qs["nonce"] == [req.nonce]
    assert qs["scope"] == ["profile openid email"]


async def test_state_mismatch_rejected_before_exchange(db_session) -> None:
    store = SqlAlchemyUserStore(db_session)
    stub = _StubProvider(OAuthProvider.GOOGLE, requires_pkce=False, identity=_google_identity())
    with pytest.raises(StateMismatchError):
        await complete_authorization(
            OAuthProvider.GOOGLE,
            code="c",
            state="attacker-state",
            stored_state="real-state",
            store=store,
            settings=_settings(),
            client=stub,
        )
    assert stub.exchanged_verifier is None
    assert stub.verified_nonce is None


async def test_successful_login_upserts_identity(db_session) -> None:
    store = SqlAlchemyUserStore(db_session)
    stub = _StubProvider(OAuthProvider.GOOGLE, requires_pkce=False, identity=_google_identity())
    user = await complete_authorization(
        OAuthProvider.GOOGLE,
        code="c",
        state="s",
        stored_state="s",
        store=store,
        settings=_settings(),
        client=stub,
    )
    assert user.email == "a@x.com"
    assert user.display_name == "Ann"
    providers = {p.provider for p in await store.list_providers(user_id=user.id)}
    assert providers == {OAuthProvider.GOOGLE}


async def test_line_verifier_and_nonce_passed_through(db_session) -> None:
    identity = VerifiedIdentity(
        provider=OAuthProvider.LINE, provider_account_id="l-sub", email=None
    )
    store = SqlAlchemyUserStore(db_session)
    stub = _StubProvider(OAuthProvider.LINE, requires_pkce=True, identity=identity)
    await complete_authorization(
        OAuthProvider.LINE,
        code="c",
        state="s",
        stored_state="s",
        store=store,
        settings=_settings(),
        stored_verifier="the-verifier",
        stored_nonce="the-nonce",
        client=stub,
    )
    assert stub.exchanged_verifier == "the-verifier"
    assert stub.verified_nonce == "the-nonce"


async def test_link_to_current_user_path(db_session) -> None:
    store = SqlAlchemyUserStore(db_session)
    existing = await store.upsert_via_oauth(
        provider=OAuthProvider.GOOGLE,
        provider_account_id="g-sub-1",
        email="a@x.com",
        email_verified=True,
    )
    line_identity = VerifiedIdentity(
        provider=OAuthProvider.LINE, provider_account_id="l-sub-9", email=None
    )
    stub = _StubProvider(OAuthProvider.LINE, requires_pkce=True, identity=line_identity)
    user = await complete_authorization(
        OAuthProvider.LINE,
        code="c",
        state="s",
        stored_state="s",
        store=store,
        settings=_settings(),
        stored_verifier="v",
        stored_nonce="n",
        current_user_id=existing.id,
        client=stub,
    )
    assert user.id == existing.id
    providers = {p.provider for p in await store.list_providers(user_id=existing.id)}
    assert providers == {OAuthProvider.GOOGLE, OAuthProvider.LINE}


async def test_missing_id_token_rejected(db_session) -> None:
    class _NoIdToken(_StubProvider):
        async def exchange_code(
            self, *, code: str, code_verifier: str | None = None
        ) -> ProviderTokenResponse:
            return ProviderTokenResponse(access_token="at", id_token=None)

    store = SqlAlchemyUserStore(db_session)
    stub = _NoIdToken(OAuthProvider.GOOGLE, requires_pkce=False, identity=_google_identity())
    with pytest.raises(IdentityVerificationError):
        await complete_authorization(
            OAuthProvider.GOOGLE,
            code="c",
            state="s",
            stored_state="s",
            store=store,
            settings=_settings(),
            client=stub,
        )


def test_line_retry_url_appends_disable_auto_login() -> None:
    url = "https://access.line.me/oauth2/v2.1/authorize?client_id=x&state=y"
    retried = line_retry_authorization_url(url)
    assert retried == url + "&disable_auto_login=true"
    # Idempotent.
    assert line_retry_authorization_url(retried) == retried


def test_line_retry_url_handles_no_query() -> None:
    assert line_retry_authorization_url("https://x/authorize") == (
        "https://x/authorize?disable_auto_login=true"
    )
