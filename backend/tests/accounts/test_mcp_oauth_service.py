"""The authorization server against a real (SQLite) database: client registration, the Client ID
Metadata Document path, consent, the authorization code, refresh-token rotation and revocation.

Each test drives the same entry points the HTTP routes call, so the rules under test are the ones
that run in production: PKCE, one-time codes with a 60 s life and a binding to client + redirect
URI + resource, rotating refresh tokens whose replay revokes the grant, hashed storage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.mcp_oauth.rules import s256_challenge
from accounts.mcp_oauth.service import (
    AuthorizationServer,
    AuthorizeRejectedError,
    ClientInfo,
    ConsentError,
    IssuedTokens,
    OAuthProtocolError,
    grant_is_active,
    hash_secret,
)
from accounts.mcp_tokens import McpTokenError, verify_mcp_access_token
from config.settings import Settings
from dataaccess.oauth_client_metadata import ClientMetadataFetchError
from db.models import (
    OAuthAuthCodeRow,
    OAuthAuthorizationRequestRow,
    OAuthClientRow,
    OAuthGrantRow,
    OAuthRefreshTokenRow,
)
from tests.accounts.conftest import make_user

ISSUER = "https://api.example.com"
RESOURCE = "https://mcp.example.com/mcp"
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = s256_challenge(VERIFIER)
LOOPBACK = "http://localhost:8787/callback"
CIMD_URL = "https://claude.ai/oauth/claude-code-client-metadata"


def make_settings(**overrides: object) -> Settings:
    return Settings(
        jwt_secret_key="test-secret-not-a-real-key-0123456789abcdef",
        oauth_issuer=ISSUER,
        mcp_public_base_url="https://mcp.example.com",
        _env_file=None,  # type: ignore[call-arg]
        **overrides,  # type: ignore[arg-type]
    )


class FakeMetadataSource:
    """A `ClientMetadataSource` that serves canned documents and counts fetches."""

    def __init__(self, documents: dict[str, dict[str, object]] | None = None) -> None:
        self.documents = documents or {}
        self.fetched: list[str] = []
        self.fail_with: ClientMetadataFetchError | None = None

    async def fetch(self, url: str) -> dict[str, object]:
        self.fetched.append(url)
        if self.fail_with is not None:
            raise self.fail_with
        return self.documents[url]


def cimd_document(**overrides: object) -> dict[str, object]:
    return {
        "client_id": CIMD_URL,
        "client_name": "Claude Code",
        "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        **overrides,
    }


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def source() -> FakeMetadataSource:
    return FakeMetadataSource({CIMD_URL: cimd_document()})


@pytest.fixture
def server(
    db_session: AsyncSession, settings: Settings, source: FakeMetadataSource
) -> AuthorizationServer:
    return AuthorizationServer(db_session, settings, metadata_source=source)


async def register(server: AuthorizationServer, uri: str = LOOPBACK) -> ClientInfo:
    return await server.register_client(client_name="Test client", redirect_uris=[uri])


async def begin(server: AuthorizationServer, client: ClientInfo, **overrides: str | None) -> str:
    params: dict[str, str | None] = {
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uris[0],
        "response_type": "code",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "resource": RESOURCE,
        "scope": "mcp:read",
        "state": "xyz",
        **overrides,
    }
    return await server.begin_authorization(**params)  # type: ignore[arg-type]


async def approve(server: AuthorizationServer, request_id: str, user_id: str) -> str:
    view = await server.consent_view(request_id)
    return await server.decide(
        request_id=request_id, user_id=user_id, approve=True, nonce=view.nonce
    )


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


async def get_code(
    server: AuthorizationServer, client: ClientInfo, user_id: str, **overrides: str | None
) -> str:
    redirect = await approve(server, await begin(server, client, **overrides), user_id)
    return query_of(redirect)["code"]


async def redeem(
    server: AuthorizationServer, client: ClientInfo, code: str, **overrides: str | None
) -> IssuedTokens:
    params: dict[str, str | None] = {
        "client_id": client.client_id,
        "code": code,
        "redirect_uri": client.redirect_uris[0],
        "code_verifier": VERIFIER,
        "resource": RESOURCE,
        **overrides,
    }
    return await server.exchange_code(**params)  # type: ignore[arg-type]


async def signed_in_tokens(
    server: AuthorizationServer, session: AsyncSession
) -> tuple[ClientInfo, str, IssuedTokens]:
    client = await register(server)
    user_id = await make_user(session)
    tokens = await redeem(server, client, await get_code(server, client, user_id))
    return client, user_id, tokens


async def protocol_error(awaitable: object) -> OAuthProtocolError:
    with pytest.raises(OAuthProtocolError) as excinfo:
        await awaitable  # type: ignore[misc]
    return excinfo.value


# ------------------------------------------------------------------------ registration (DCR)


async def test_registration_returns_a_public_client_and_stores_it(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await server.register_client(
        client_name="  My\x00 Client ", redirect_uris=[LOOPBACK, "https://app.example/cb"]
    )
    assert client.kind == "dcr"
    assert client.name == "My  Client"  # control characters never reach the consent screen
    row = await db_session.get(OAuthClientRow, client.client_id)
    assert row is not None
    assert row.token_endpoint_auth_method == "none"
    assert row.redirect_uris == [LOOPBACK, "https://app.example/cb"]


@pytest.mark.parametrize(
    "uri",
    ["http://example.com/cb", "javascript:alert(1)", "https://a.example/cb#x", "myapp://cb"],
)
async def test_registration_refuses_unsafe_redirect_uris(
    server: AuthorizationServer, uri: str
) -> None:
    error = await protocol_error(server.register_client(client_name="x", redirect_uris=[uri]))
    assert error.error == "invalid_redirect_uri"


async def test_registration_with_no_redirect_uris_is_refused(server: AuthorizationServer) -> None:
    error = await protocol_error(server.register_client(client_name="x", redirect_uris=[]))
    assert error.error == "invalid_redirect_uri"


async def test_an_allow_listed_desktop_scheme_can_be_registered(
    server: AuthorizationServer,
) -> None:
    client = await register(server, "vscode://vscode.github-authentication/did-authenticate")
    assert client.redirect_uris == ("vscode://vscode.github-authentication/did-authenticate",)
    other = AuthorizationServer(
        server._session,
        make_settings(oauth_extra_redirect_schemes=""),
    )
    error = await protocol_error(
        other.register_client(client_name="x", redirect_uris=["vscode://a/b"])
    )
    assert error.error == "invalid_redirect_uri"


async def test_the_client_table_has_a_cap(db_session: AsyncSession) -> None:
    capped = AuthorizationServer(db_session, make_settings(oauth_max_registered_clients=2))
    await register(capped)
    await register(capped)
    error = await protocol_error(register(capped))
    assert error.error == "temporarily_unavailable"
    assert error.status == 503


async def test_stale_clients_without_grants_are_dropped_to_make_room(
    db_session: AsyncSession,
) -> None:
    capped = AuthorizationServer(db_session, make_settings(oauth_max_registered_clients=2))
    stale = await register(capped)
    granted = await register(capped)
    user_id = await make_user(db_session)
    await redeem(capped, granted, await get_code(capped, granted, user_id))
    long_ago = datetime.now(UTC) - timedelta(days=40)
    await db_session.execute(update(OAuthClientRow).values(last_seen_at=long_ago))
    await register(capped)  # the cap is hit, so the stale, grant-less client is collected
    assert await db_session.get(OAuthClientRow, stale.client_id) is None
    assert await db_session.get(OAuthClientRow, granted.client_id) is not None  # has a grant


async def test_stale_metadata_document_clients_are_collected_when_a_new_one_arrives(
    server: AuthorizationServer, source: FakeMetadataSource, db_session: AsyncSession
) -> None:
    await server.resolve_client(CIMD_URL)
    await db_session.execute(
        update(OAuthClientRow).values(last_seen_at=datetime.now(UTC) - timedelta(days=40))
    )
    other = "https://other.example/client.json"
    source.documents[other] = cimd_document(client_id=other)
    await server.resolve_client(other)
    assert await db_session.get(OAuthClientRow, CIMD_URL) is None
    assert await db_session.get(OAuthClientRow, other) is not None


async def test_an_unknown_client_id_is_invalid_client(server: AuthorizationServer) -> None:
    error = await protocol_error(server.resolve_client("00000000-0000-0000-0000-000000000000"))
    assert error.error == "invalid_client"


# ------------------------------------------------------------------------ authorize + consent


async def test_a_valid_request_is_stored_with_a_nonce_and_a_short_life(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    request_id = await begin(server, client, resource=RESOURCE + "/")
    row = await db_session.get(OAuthAuthorizationRequestRow, request_id)
    assert row is not None
    assert row.resource == RESOURCE  # always the canonical resource
    assert row.scope == "mcp:read"
    assert len(row.nonce) >= 24
    lifetime = (row.expires_at - row.created_at).total_seconds()
    assert 0 < lifetime <= 600
    view = await server.consent_view(request_id)
    assert (view.client_name, view.redirect_host, view.scope) == (
        "Test client",
        "localhost",
        "mcp:read",
    )


async def test_the_redirect_uri_may_be_omitted_only_when_exactly_one_is_registered(
    server: AuthorizationServer,
) -> None:
    one = await register(server)
    assert await begin(server, one, redirect_uri=None)
    two = await server.register_client(
        client_name="two", redirect_uris=["https://a.example/cb", "https://b.example/cb"]
    )
    with pytest.raises(AuthorizeRejectedError) as excinfo:
        await begin(server, two, redirect_uri=None)
    assert excinfo.value.redirect_uri is None


async def test_a_loopback_request_may_use_any_port(server: AuthorizationServer) -> None:
    client = await register(server)
    assert await begin(server, client, redirect_uri="http://localhost:51999/callback")


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://localhost:8787/other",
        "https://evil.example/callback",
        "http://127.0.0.1:8787/callback",
        "javascript:alert(1)",
    ],
)
async def test_a_redirect_uri_that_is_not_registered_is_never_redirected_to(
    server: AuthorizationServer, redirect_uri: str
) -> None:
    client = await register(server)
    with pytest.raises(AuthorizeRejectedError) as excinfo:
        await begin(server, client, redirect_uri=redirect_uri)
    assert excinfo.value.redirect_uri is None  # an open redirect would send the user there
    assert excinfo.value.error == "invalid_request"


async def test_an_unknown_client_is_not_redirected_either(server: AuthorizationServer) -> None:
    client = await register(server)
    with pytest.raises(AuthorizeRejectedError) as excinfo:
        await begin(server, client, client_id="nope")
    assert excinfo.value.redirect_uri is None
    assert excinfo.value.error == "invalid_client"


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"response_type": None}, "unsupported_response_type"),
        ({"code_challenge": None}, "invalid_request"),
        ({"code_challenge": "too-short"}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge_method": None}, "invalid_request"),
        ({"resource": "https://evil.example/mcp"}, "invalid_target"),
        ({"state": "s" * 3000}, "invalid_request"),
    ],
)
async def test_other_defects_go_back_to_the_verified_redirect_uri(
    server: AuthorizationServer, override: dict[str, str | None], error: str
) -> None:
    client = await register(server)
    with pytest.raises(AuthorizeRejectedError) as excinfo:
        await begin(server, client, **override)
    assert excinfo.value.error == error
    assert excinfo.value.redirect_uri == client.redirect_uris[0]


async def test_an_absent_resource_defaults_to_this_server_and_odd_scopes_are_ignored(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    request_id = await begin(server, client, resource=None, scope="openid offline_access")
    row = await db_session.get(OAuthAuthorizationRequestRow, request_id)
    assert row is not None
    assert (row.resource, row.scope) == (RESOURCE, "mcp:read")


async def test_approval_returns_a_code_state_and_issuer_on_the_redirect_uri(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    redirect = await approve(server, await begin(server, client), user_id)
    assert redirect.startswith(LOOPBACK + "?")
    params = query_of(redirect)
    assert params["state"] == "xyz"
    assert params["iss"] == ISSUER  # RFC 9207
    row = await db_session.get(OAuthAuthCodeRow, hash_secret(params["code"]))
    assert row is not None
    assert row.user_id == user_id
    assert (row.expires_at - row.created_at).total_seconds() == 60


async def test_denial_redirects_with_access_denied_and_issues_nothing(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    request_id = await begin(server, client)
    nonce = (await server.consent_view(request_id)).nonce
    redirect = await server.decide(
        request_id=request_id, user_id=user_id, approve=False, nonce=nonce
    )
    params = query_of(redirect)
    assert params["error"] == "access_denied"
    assert (params["state"], params["iss"]) == ("xyz", ISSUER)
    assert "code" not in params
    assert (await db_session.scalars(select(OAuthAuthCodeRow))).all() == []


async def test_a_decision_without_the_right_nonce_is_refused_and_changes_nothing(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    request_id = await begin(server, client)
    for wrong in ("", "guess", "x" * 500):
        with pytest.raises(ConsentError) as excinfo:
            await server.decide(request_id=request_id, user_id=user_id, approve=True, nonce=wrong)
        assert excinfo.value.failure == "nonce_mismatch"
    assert (await db_session.scalars(select(OAuthAuthCodeRow))).all() == []
    assert await approve(server, request_id, user_id)  # the real nonce still works


async def test_a_request_can_be_decided_only_once(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    request_id = await begin(server, client)
    nonce = (await server.consent_view(request_id)).nonce
    await server.decide(request_id=request_id, user_id=user_id, approve=True, nonce=nonce)
    with pytest.raises(ConsentError) as excinfo:
        await server.decide(request_id=request_id, user_id=user_id, approve=True, nonce=nonce)
    assert excinfo.value.failure == "not_found"
    with pytest.raises(ConsentError):
        await server.consent_view(request_id)


async def test_an_expired_request_cannot_be_viewed_or_decided(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    request_id = await begin(server, client)
    nonce = (await server.consent_view(request_id)).nonce
    await db_session.execute(
        update(OAuthAuthorizationRequestRow).values(
            expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
    )
    with pytest.raises(ConsentError):
        await server.consent_view(request_id)
    with pytest.raises(ConsentError):
        await server.decide(request_id=request_id, user_id=user_id, approve=True, nonce=nonce)


# ------------------------------------------------------------------------ code redemption


async def test_a_code_is_exchanged_for_tokens_bound_to_the_resource(
    server: AuthorizationServer, db_session: AsyncSession, settings: Settings
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    tokens = await redeem(server, client, await get_code(server, client, user_id))
    claims = verify_mcp_access_token(
        tokens.access_token, settings=settings, issuer=ISSUER, resource=RESOURCE
    )
    assert (claims.user_id, claims.client_id, claims.scope) == (
        user_id,
        client.client_id,
        "mcp:read",
    )
    assert tokens.expires_in == 600
    assert tokens.scope == "mcp:read"
    assert await grant_is_active(db_session, grant_id=claims.grant_id, user_id=user_id)


async def test_codes_and_refresh_tokens_are_stored_only_as_hashes(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    code = await get_code(server, client, user_id)
    tokens = await redeem(server, client, code)
    stored = [
        *(await db_session.scalars(select(OAuthAuthCodeRow.code_hash))).all(),
        *(await db_session.scalars(select(OAuthRefreshTokenRow.token_hash))).all(),
    ]
    assert hash_secret(code) in stored
    assert hash_secret(tokens.refresh_token) in stored
    assert code not in stored
    assert tokens.refresh_token not in stored
    assert len(stored) == 2
    assert all(len(h) == 64 for h in stored)  # SHA-256 hex


async def test_a_code_works_once_and_its_replay_revokes_the_grant(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    code = await get_code(server, client, user_id)
    first = await redeem(server, client, code)
    error = await protocol_error(redeem(server, client, code))
    assert error.error == "invalid_grant"
    # RFC 6749 4.1.2: what the first redemption produced is revoked.
    refresh_error = await protocol_error(
        server.refresh(
            client_id=client.client_id,
            refresh_token=first.refresh_token,
            scope=None,
            resource=None,
        )
    )
    assert refresh_error.error == "invalid_grant"
    grant = (await db_session.scalars(select(OAuthGrantRow))).one()
    assert grant.revoked_at is not None


async def test_an_expired_code_is_refused(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    code = await get_code(server, client, user_id)
    await db_session.execute(
        update(OAuthAuthCodeRow).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    assert (await protocol_error(redeem(server, client, code))).error == "invalid_grant"
    assert (await db_session.scalars(select(OAuthGrantRow))).all() == []


async def test_a_code_that_was_never_issued_is_refused(server: AuthorizationServer) -> None:
    client = await register(server)
    assert (await protocol_error(redeem(server, client, "made-up"))).error == "invalid_grant"


async def test_a_code_is_bound_to_its_client_redirect_uri_verifier_and_resource(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    other = await register(server, "https://other.example/cb")
    user_id = await make_user(db_session)
    attempts: list[tuple[ClientInfo, dict[str, str | None], str]] = [
        (client, {"client_id": other.client_id}, "invalid_grant"),
        (client, {"redirect_uri": "https://evil.example/cb"}, "invalid_grant"),
        (client, {"redirect_uri": None}, "invalid_grant"),
        (client, {"code_verifier": "A" * 43}, "invalid_grant"),
        (client, {"code_verifier": None}, "invalid_grant"),
        (client, {"resource": "https://evil.example/mcp"}, "invalid_target"),
    ]
    for owner, override, expected in attempts:
        code = await get_code(server, owner, user_id)
        error = await protocol_error(redeem(server, owner, code, **override))
        assert error.error == expected, override
    # A failed attempt does not burn the code for its rightful owner.
    code = await get_code(server, client, user_id)
    await protocol_error(redeem(server, client, code, code_verifier="B" * 43))
    assert (await redeem(server, client, code)).access_token


async def test_a_loopback_client_redeems_with_the_exact_uri_it_authorized_with(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    uri = "http://localhost:51999/callback"
    code = await get_code(server, client, user_id, redirect_uri=uri)
    assert (await protocol_error(redeem(server, client, code))).error == "invalid_grant"
    code = await get_code(server, client, user_id, redirect_uri=uri)
    assert (await redeem(server, client, code, redirect_uri=uri)).access_token


# ------------------------------------------------------------------------ refresh tokens


async def test_a_refresh_token_rotates_on_every_use(
    server: AuthorizationServer, db_session: AsyncSession, settings: Settings
) -> None:
    client, user_id, first = await signed_in_tokens(server, db_session)
    second = await server.refresh(
        client_id=client.client_id, refresh_token=first.refresh_token, scope=None, resource=RESOURCE
    )
    assert second.refresh_token != first.refresh_token
    claims = verify_mcp_access_token(
        second.access_token, settings=settings, issuer=ISSUER, resource=RESOURCE
    )
    assert claims.user_id == user_id
    third = await server.refresh(
        client_id=client.client_id, refresh_token=second.refresh_token, scope=None, resource=None
    )
    assert third.refresh_token not in {first.refresh_token, second.refresh_token}
    rows = (await db_session.scalars(select(OAuthRefreshTokenRow))).all()
    assert sum(r.rotated_at is None for r in rows) == 1  # exactly one live token
    grant = (await db_session.scalars(select(OAuthGrantRow))).one()
    assert grant.last_used_at is not None
    assert grant.revoked_at is None


async def test_replaying_a_rotated_refresh_token_revokes_the_whole_grant(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client, user_id, first = await signed_in_tokens(server, db_session)
    second = await server.refresh(
        client_id=client.client_id, refresh_token=first.refresh_token, scope=None, resource=None
    )
    replay = await protocol_error(
        server.refresh(
            client_id=client.client_id, refresh_token=first.refresh_token, scope=None, resource=None
        )
    )
    assert replay.error == "invalid_grant"
    # The newest token - the legitimate holder's - died with the grant.
    after = await protocol_error(
        server.refresh(
            client_id=client.client_id,
            refresh_token=second.refresh_token,
            scope=None,
            resource=None,
        )
    )
    assert after.error == "invalid_grant"
    grant = (await db_session.scalars(select(OAuthGrantRow))).one()
    assert grant.revoked_at is not None
    assert not await grant_is_active(db_session, grant_id=grant.id, user_id=user_id)
    assert (await db_session.scalars(select(OAuthRefreshTokenRow))).all() == []


async def test_a_refresh_token_is_bound_to_its_client_scope_and_resource(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client, _, tokens = await signed_in_tokens(server, db_session)
    other = await register(server, "https://other.example/cb")
    cases = [
        ({"client_id": other.client_id}, "invalid_grant"),
        ({"scope": "mcp:read admin"}, "invalid_scope"),
        ({"resource": "https://evil.example/mcp"}, "invalid_target"),
        ({"refresh_token": "not-a-token"}, "invalid_grant"),
    ]
    for override, expected in cases:
        params: dict[str, str | None] = {
            "client_id": client.client_id,
            "refresh_token": tokens.refresh_token,
            "scope": None,
            "resource": None,
            **override,
        }
        error = await protocol_error(server.refresh(**params))  # type: ignore[arg-type]
        assert error.error == expected, override
    # None of those attempts used up the token.
    assert (
        await server.refresh(
            client_id=client.client_id,
            refresh_token=tokens.refresh_token,
            scope="mcp:read",
            resource=RESOURCE + "/",
        )
    ).access_token


async def test_an_expired_refresh_token_is_refused(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client, _, tokens = await signed_in_tokens(server, db_session)
    await db_session.execute(
        update(OAuthRefreshTokenRow).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    error = await protocol_error(
        server.refresh(
            client_id=client.client_id,
            refresh_token=tokens.refresh_token,
            scope=None,
            resource=None,
        )
    )
    assert error.error == "invalid_grant"


async def test_a_new_refresh_token_lives_for_the_configured_days(
    db_session: AsyncSession,
) -> None:
    short = AuthorizationServer(db_session, make_settings(oauth_refresh_token_days=3))
    _, _, _ = await signed_in_tokens(short, db_session)
    row = (await db_session.scalars(select(OAuthRefreshTokenRow))).one()
    assert (row.expires_at - row.created_at).days == 3


# ------------------------------------------------------------------------ revocation (RFC 7009)


async def test_revoking_a_refresh_token_ends_the_grant(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client, user_id, tokens = await signed_in_tokens(server, db_session)
    await server.revoke_token(token=tokens.refresh_token, client_id=client.client_id)
    grant = (await db_session.scalars(select(OAuthGrantRow))).one()
    assert grant.revoked_at is not None
    assert not await grant_is_active(db_session, grant_id=grant.id, user_id=user_id)
    error = await protocol_error(
        server.refresh(
            client_id=client.client_id,
            refresh_token=tokens.refresh_token,
            scope=None,
            resource=None,
        )
    )
    assert error.error == "invalid_grant"


async def test_revoking_an_access_token_ends_its_grant_too(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client, _, tokens = await signed_in_tokens(server, db_session)
    await server.revoke_token(token=tokens.access_token, client_id=None)
    assert (await db_session.scalars(select(OAuthGrantRow))).one().revoked_at is not None
    assert client


async def test_revoking_an_unknown_token_or_another_clients_token_does_nothing(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client, _, tokens = await signed_in_tokens(server, db_session)
    other = await register(server, "https://other.example/cb")
    await server.revoke_token(token="never-issued", client_id=None)
    await server.revoke_token(token=tokens.refresh_token, client_id=other.client_id)
    assert (await db_session.scalars(select(OAuthGrantRow))).one().revoked_at is None
    assert client


# ------------------------------------------------------------ Client ID Metadata Documents


async def test_a_metadata_document_client_is_fetched_validated_and_cached(
    server: AuthorizationServer, source: FakeMetadataSource, db_session: AsyncSession
) -> None:
    client = await server.resolve_client(CIMD_URL)
    assert (client.kind, client.name, client.client_id) == ("cimd", "Claude Code", CIMD_URL)
    assert client.redirect_uris == ("http://localhost/callback", "http://127.0.0.1/callback")
    await server.resolve_client(CIMD_URL)
    assert source.fetched == [CIMD_URL]  # the second lookup is served from the cache
    await db_session.execute(
        update(OAuthClientRow).values(metadata_fetched_at=datetime.now(UTC) - timedelta(hours=1))
    )
    source.documents[CIMD_URL] = cimd_document(client_name="Renamed")
    assert (await server.resolve_client(CIMD_URL)).name == "Renamed"
    assert source.fetched == [CIMD_URL, CIMD_URL]


async def test_a_metadata_document_client_completes_the_whole_flow(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await server.resolve_client(CIMD_URL)
    user_id = await make_user(db_session)
    uri = "http://localhost:43210/callback"
    tokens = await redeem(
        server,
        client,
        await get_code(server, client, user_id, redirect_uri=uri),
        redirect_uri=uri,
    )
    assert tokens.refresh_token


@pytest.mark.parametrize(
    "document",
    [
        cimd_document(client_id="https://claude.ai/some/other/document"),
        cimd_document(client_secret="s3cret"),
        cimd_document(token_endpoint_auth_method="client_secret_basic"),
        cimd_document(grant_types=["client_credentials"]),
        cimd_document(redirect_uris=["http://example.com/cb"]),
        cimd_document(redirect_uris=["javascript:alert(1)"]),
        cimd_document(redirect_uris=[]),
        cimd_document(redirect_uris="http://localhost/cb"),
        cimd_document(redirect_uris=[1, 2]),
        {"client_name": "no client_id at all", "redirect_uris": ["http://localhost/cb"]},
    ],
)
async def test_an_invalid_metadata_document_is_refused(
    server: AuthorizationServer, source: FakeMetadataSource, document: dict[str, object]
) -> None:
    source.documents[CIMD_URL] = document
    error = await protocol_error(server.resolve_client(CIMD_URL))
    assert error.error == "invalid_client"


async def test_a_failed_fetch_is_an_invalid_client_and_is_not_cached(
    server: AuthorizationServer, source: FakeMetadataSource
) -> None:
    source.fail_with = ClientMetadataFetchError("blocked_address")
    error = await protocol_error(server.resolve_client(CIMD_URL))
    assert error.error == "invalid_client"
    assert "blocked_address" in error.description
    source.fail_with = None
    assert (await server.resolve_client(CIMD_URL)).kind == "cimd"


@pytest.mark.parametrize(
    "client_id",
    [
        "https://app.example.com",  # no path: not a document URL
        "http://app.example.com/client.json",
        "https://10.0.0.5/client.json",
        "https://u:p@app.example.com/client.json",
    ],
)
async def test_urls_that_cannot_be_metadata_documents_are_never_fetched(
    server: AuthorizationServer, source: FakeMetadataSource, client_id: str
) -> None:
    error = await protocol_error(server.resolve_client(client_id))
    assert error.error == "invalid_client"
    assert source.fetched == []


async def test_metadata_documents_are_off_without_a_fetcher(
    db_session: AsyncSession, settings: Settings
) -> None:
    error = await protocol_error(AuthorizationServer(db_session, settings).resolve_client(CIMD_URL))
    assert error.error == "invalid_client"


async def test_an_authorize_request_for_a_bad_metadata_client_is_not_redirected(
    server: AuthorizationServer, source: FakeMetadataSource
) -> None:
    source.fail_with = ClientMetadataFetchError("redirect")
    with pytest.raises(AuthorizeRejectedError) as excinfo:
        await server.begin_authorization(
            client_id=CIMD_URL,
            redirect_uri="http://localhost:1/callback",
            response_type="code",
            code_challenge=CHALLENGE,
            code_challenge_method="S256",
            resource=RESOURCE,
            scope=None,
            state=None,
        )
    assert excinfo.value.redirect_uri is None


# ------------------------------------------------------------------------ tokens and configuration


async def test_an_access_token_for_another_resource_or_issuer_does_not_verify(
    server: AuthorizationServer, db_session: AsyncSession, settings: Settings
) -> None:
    _, _, tokens = await signed_in_tokens(server, db_session)
    for issuer, resource in (
        (ISSUER, "https://other.example/mcp"),
        ("https://other.example", RESOURCE),
    ):
        with pytest.raises(McpTokenError):
            verify_mcp_access_token(
                tokens.access_token, settings=settings, issuer=issuer, resource=resource
            )


async def test_an_unconfigured_server_refuses_to_issue(db_session: AsyncSession) -> None:
    unconfigured = AuthorizationServer(
        db_session,
        Settings(
            jwt_secret_key="test-secret-not-a-real-key-0123456789abcdef",
            _env_file=None,  # type: ignore[call-arg]
        ),
    )
    with pytest.raises(OAuthProtocolError) as excinfo:
        _ = unconfigured.issuer
    assert excinfo.value.status == 503
