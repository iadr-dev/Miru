"""MCP access tokens: audience, issuer, type and key separation from the app's session JWT."""

from __future__ import annotations

import time

import jwt
import pytest

from accounts.auth import InvalidAccessTokenError, decode_access_token, issue_access_token
from accounts.mcp_tokens import (
    McpTokenError,
    _signing_key,
    issue_mcp_access_token,
    verify_mcp_access_token,
)
from config.settings import Settings

ISSUER = "https://api.example.com"
RESOURCE = "https://mcp.example.com/mcp"
SECRET = "test-secret-not-a-real-key-0123456789abcdef"


@pytest.fixture
def settings() -> Settings:
    return Settings(jwt_secret_key=SECRET, _env_file=None)  # type: ignore[call-arg]


def issue(settings: Settings, **overrides: str) -> str:
    claims = {
        "user_id": "user-1",
        "client_id": "client-1",
        "grant_id": "grant-1",
        "scope": "mcp:read",
        "issuer": ISSUER,
        "resource": RESOURCE,
        **overrides,
    }
    return issue_mcp_access_token(settings=settings, **claims)[0]  # type: ignore[arg-type]


def verify(settings: Settings, token: str, **overrides: str):
    args = {"issuer": ISSUER, "resource": RESOURCE, **overrides}
    return verify_mcp_access_token(token, settings=settings, **args)


def reason(settings: Settings, token: str, **overrides: str) -> str:
    with pytest.raises(McpTokenError) as excinfo:
        verify(settings, token, **overrides)
    return excinfo.value.reason


def resign(settings: Settings, token: str, **changes: object) -> str:
    claims = jwt.decode(token, options={"verify_signature": False})
    claims.update(changes)
    return jwt.encode(
        {k: v for k, v in claims.items() if v is not None}, _signing_key(settings), "HS256"
    )


def test_a_fresh_token_verifies_and_names_the_user_client_and_grant(settings: Settings) -> None:
    token = issue(settings)
    claims = verify(settings, token)
    assert (claims.user_id, claims.client_id, claims.grant_id, claims.scope) == (
        "user-1",
        "client-1",
        "grant-1",
        "mcp:read",
    )
    assert claims.expires_at.timestamp() > time.time()


def test_the_lifetime_comes_from_settings(settings: Settings) -> None:
    short = Settings(jwt_secret_key=SECRET, oauth_access_token_minutes=2, _env_file=None)  # type: ignore[call-arg]
    _, seconds = issue_mcp_access_token(
        user_id="u",
        client_id="c",
        grant_id="g",
        scope="mcp:read",
        issuer=ISSUER,
        resource=RESOURCE,
        settings=short,
    )
    assert seconds == 120


def test_the_audience_and_issuer_must_match_exactly(settings: Settings) -> None:
    token = issue(settings)
    assert reason(settings, token, resource="https://mcp.example.com/other") == "invalid"
    assert reason(settings, token, resource="https://evil.example/mcp") == "invalid"
    assert reason(settings, token, issuer="https://evil.example") == "invalid"
    assert reason(settings, issue(settings, resource="https://evil.example/mcp")) == "invalid"


def test_an_expired_token_is_reported_as_expired(settings: Settings) -> None:
    stale = resign(settings, issue(settings), exp=int(time.time()) - 3600)
    assert reason(settings, stale) == "expired"


def test_the_wrong_type_missing_claims_and_wrong_scope_are_refused(settings: Settings) -> None:
    token = issue(settings)
    assert reason(settings, resign(settings, token, typ="access")) == "invalid"
    assert reason(settings, resign(settings, token, typ=None)) == "invalid"
    assert reason(settings, resign(settings, token, jti=None)) == "invalid"
    assert reason(settings, resign(settings, token, gid=None)) == "invalid"
    assert reason(settings, resign(settings, token, scope="other")) == "invalid"
    assert reason(settings, resign(settings, token, sub=123)) == "invalid"


def test_garbage_and_foreign_signatures_are_refused(settings: Settings) -> None:
    assert reason(settings, "garbage") == "invalid"
    assert reason(settings, "") == "invalid"
    claims = jwt.decode(issue(settings), options={"verify_signature": False})
    for key in (SECRET, "another-secret-0123456789abcdef0123456789"):
        assert reason(settings, jwt.encode(claims, key, "HS256")) == "invalid"
    unsigned = jwt.encode(claims, key=None, algorithm="none")
    assert reason(settings, unsigned) == "invalid"


def test_the_apps_session_jwt_is_not_an_mcp_token(settings: Settings) -> None:
    session_jwt = issue_access_token(user_id="user-1", settings=settings)
    assert reason(settings, session_jwt) == "invalid"


def test_an_mcp_token_is_not_an_app_session(settings: Settings) -> None:
    token = issue(settings)
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(token, settings=settings)


def test_a_token_signed_with_the_raw_secret_is_refused_even_if_every_claim_is_right(
    settings: Settings,
) -> None:
    claims = jwt.decode(issue(settings), options={"verify_signature": False})
    assert reason(settings, jwt.encode(claims, SECRET, "HS256")) == "invalid"


def test_two_tokens_differ_even_for_the_same_grant(settings: Settings) -> None:
    first, second = issue(settings), issue(settings)
    assert first != second  # the `jti` is random
    claims = [jwt.decode(t, options={"verify_signature": False}) for t in (first, second)]
    assert claims[0]["jti"] != claims[1]["jti"]


def test_without_a_signing_secret_nothing_verifies() -> None:
    unconfigured = Settings(jwt_secret_key=None, _env_file=None)  # type: ignore[call-arg]
    assert reason(unconfigured, "anything") == "invalid"
