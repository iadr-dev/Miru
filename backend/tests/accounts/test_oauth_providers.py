"""Provider clients: authorization-URL params, code exchange over a stubbed httpx transport,
Google RS256/JWKS ID-token verification with a locally-generated key, and LINE `/verify`-based
verification including nonce/issuer/audience rejection. No live provider calls."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from accounts.models import OAuthProvider
from accounts.oauth_providers import (
    GoogleOAuthProvider,
    IdentityVerificationError,
    LineOAuthProvider,
    OAuthConfigError,
    TokenExchangeError,
    build_provider_client,
)
from config.settings import Settings


def _settings() -> Settings:
    return Settings(
        google_client_id="g-client",
        google_client_secret="g-secret",
        google_redirect_uri="https://app.example/cb/google",
        line_channel_id="line-channel",
        line_channel_secret="line-secret",
        line_redirect_uri="https://app.example/cb/line",
    )


def _mock_client_factory(handler: Any):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


class _StubSigningKey:
    def __init__(self, key: Any) -> None:
        self.key = key


class _StubSigningKeyClient:
    def __init__(self, key: Any) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> _StubSigningKey:
        return _StubSigningKey(self._key)


def test_missing_google_config_raises() -> None:
    bare = Settings(google_client_id=None, google_client_secret=None, google_redirect_uri=None)
    with pytest.raises(OAuthConfigError):
        GoogleOAuthProvider(bare)


def test_google_authorization_url_params() -> None:
    provider = GoogleOAuthProvider(_settings())
    url = provider.build_authorization_url(state="st8")
    qs = parse_qs(urlparse(url).query)
    assert qs["client_id"] == ["g-client"]
    assert qs["scope"] == ["openid email profile"]
    assert qs["access_type"] == ["offline"]
    assert qs["state"] == ["st8"]
    assert "code_challenge" not in qs


def test_line_authorization_url_requires_pkce_and_nonce() -> None:
    provider = LineOAuthProvider(_settings())
    with pytest.raises(OAuthConfigError):
        provider.build_authorization_url(state="s", code_challenge=None, nonce="n")
    with pytest.raises(OAuthConfigError):
        provider.build_authorization_url(state="s", code_challenge="c", nonce=None)
    url = provider.build_authorization_url(state="s", code_challenge="chal", nonce="non")
    qs = parse_qs(urlparse(url).query)
    assert qs["code_challenge"] == ["chal"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["nonce"] == ["non"]


async def test_google_code_exchange_parses_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "oauth2.googleapis.com/token" in str(request.url)
        return httpx.Response(200, json={"access_token": "at", "id_token": "idt"})

    provider = GoogleOAuthProvider(_settings(), client_factory=_mock_client_factory(handler))
    tokens = await provider.exchange_code(code="c")
    assert tokens.access_token == "at"
    assert tokens.id_token == "idt"


async def test_token_exchange_error_on_http_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    provider = GoogleOAuthProvider(_settings(), client_factory=_mock_client_factory(handler))
    with pytest.raises(TokenExchangeError):
        await provider.exchange_code(code="bad")


async def test_line_exchange_requires_verifier() -> None:
    provider = LineOAuthProvider(_settings())
    with pytest.raises(TokenExchangeError):
        await provider.exchange_code(code="c", code_verifier=None)


async def test_google_verify_id_token_rs256() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {
            "sub": "google-sub-1",
            "aud": "g-client",
            "iss": "https://accounts.google.com",
            "exp": int(time.time()) + 300,
            "email": "a@x.com",
            "email_verified": True,
            "name": "Ann",
            "picture": "https://img/a.png",
        },
        key,
        algorithm="RS256",
    )
    provider = GoogleOAuthProvider(
        _settings(), signing_key_client=_StubSigningKeyClient(key.public_key())
    )
    identity = await provider.verify_id_token(token)
    assert identity.provider is OAuthProvider.GOOGLE
    assert identity.provider_account_id == "google-sub-1"
    assert identity.email == "a@x.com"
    assert identity.email_verified is True


async def test_google_verify_rejects_wrong_audience() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {
            "sub": "s",
            "aud": "someone-else",
            "iss": "https://accounts.google.com",
            "exp": int(time.time()) + 300,
        },
        key,
        algorithm="RS256",
    )
    provider = GoogleOAuthProvider(
        _settings(), signing_key_client=_StubSigningKeyClient(key.public_key())
    )
    with pytest.raises(IdentityVerificationError):
        await provider.verify_id_token(token)


def _line_verify_factory(claims: dict[str, Any]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.line.me/oauth2/v2.1/verify" in str(request.url)
        return httpx.Response(200, json=claims)

    return _mock_client_factory(handler)


async def test_line_verify_id_token_ok() -> None:
    claims = {
        "iss": "https://access.line.me",
        "aud": "line-channel",
        "sub": "line-user-1",
        "nonce": "the-nonce",
        "name": "Bob",
    }
    provider = LineOAuthProvider(_settings(), client_factory=_line_verify_factory(claims))
    identity = await provider.verify_id_token("idt", nonce="the-nonce")
    assert identity.provider_account_id == "line-user-1"
    assert identity.email is None
    assert identity.email_verified is False


async def test_line_verify_rejects_nonce_mismatch() -> None:
    claims = {
        "iss": "https://access.line.me",
        "aud": "line-channel",
        "sub": "line-user-1",
        "nonce": "server-sent-nonce",
    }
    provider = LineOAuthProvider(_settings(), client_factory=_line_verify_factory(claims))
    with pytest.raises(IdentityVerificationError):
        await provider.verify_id_token("idt", nonce="different-nonce")


async def test_line_verify_rejects_bad_issuer() -> None:
    claims = {"iss": "https://evil.example", "aud": "line-channel", "sub": "x"}
    provider = LineOAuthProvider(_settings(), client_factory=_line_verify_factory(claims))
    with pytest.raises(IdentityVerificationError):
        await provider.verify_id_token("idt", nonce=None)


def test_build_provider_client_dispatch() -> None:
    settings = _settings()
    assert isinstance(build_provider_client(OAuthProvider.GOOGLE, settings), GoogleOAuthProvider)
    assert isinstance(build_provider_client(OAuthProvider.LINE, settings), LineOAuthProvider)
