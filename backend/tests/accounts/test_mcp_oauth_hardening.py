# ruff: noqa: F811 - pytest fixtures imported from the sibling module and used as parameters
"""Hardening of the authorization server found in security review: the consent screen is bound to
the browser and the user that opened it, the redirect destination is exactly what was validated,
a replayed code cannot be used to cut someone off, and unauthenticated callers cannot grow state
or tie up connections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.mcp_oauth.service import (
    AuthorizationServer,
    AuthorizeRejectedError,
    ConsentError,
    build_redirect,
    hash_secret,
)
from config.settings import Settings
from db.models import (
    OAuthAuthCodeRow,
    OAuthAuthorizationRequestRow,
    OAuthClientRow,
    OAuthGrantRow,
)
from tests.accounts.conftest import make_user
from tests.accounts.test_mcp_oauth_service import (  # noqa: F401 - fixtures
    BROWSERS,
    CIMD_URL,
    FakeMetadataSource,
    approve,
    begin,
    cimd_document,
    get_code,
    make_settings,
    protocol_error,
    query_of,
    redeem,
    register,
    server,
    settings,
    source,
)

# ------------------------------------------------------------ consent binding (browser + user)


async def test_consent_is_only_served_to_the_browser_that_started_the_flow(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    request_id = await begin(server, client)
    for secret in (None, "", "guess", BROWSERS[request_id] + "x"):
        with pytest.raises(ConsentError) as excinfo:
            await server.consent_view(request_id, user_id=user_id, browser_secret=secret)
        assert excinfo.value.failure == "browser_mismatch"
        with pytest.raises(ConsentError) as excinfo:
            await server.decide(
                request_id=request_id,
                user_id=user_id,
                approve=True,
                nonce="x",
                browser_secret=secret,
            )
        assert excinfo.value.failure == "browser_mismatch"
    row = await db_session.get(OAuthAuthorizationRequestRow, request_id)
    assert row is not None
    assert row.bound_user_id is None  # a refused browser binds nobody
    assert row.browser_hash == hash_secret(BROWSERS[request_id])  # only the hash is stored
    assert BROWSERS[request_id] not in {row.browser_hash, row.nonce}
    assert await approve(server, request_id, user_id)  # the right browser still works


async def test_the_first_user_to_open_the_consent_screen_owns_it(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    owner = await make_user(db_session)
    other = await make_user(db_session)
    request_id = await begin(server, client)
    secret = BROWSERS[request_id]
    view = await server.consent_view(request_id, user_id=owner, browser_secret=secret)
    with pytest.raises(ConsentError) as excinfo:
        await server.consent_view(request_id, user_id=other, browser_secret=secret)
    assert excinfo.value.failure == "user_mismatch"
    with pytest.raises(ConsentError) as excinfo:
        await server.decide(
            request_id=request_id,
            user_id=other,
            approve=True,
            nonce=view.nonce,
            browser_secret=secret,
        )
    assert excinfo.value.failure == "user_mismatch"
    assert (await db_session.scalars(select(OAuthAuthCodeRow))).all() == []
    redirect = await server.decide(
        request_id=request_id,
        user_id=owner,
        approve=True,
        nonce=view.nonce,
        browser_secret=secret,
    )
    assert "code" in query_of(redirect)


async def test_opening_the_screen_binds_the_user(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    request_id = await begin(server, client)
    await server.consent_view(request_id, user_id=user_id, browser_secret=BROWSERS[request_id])
    row = await db_session.get(OAuthAuthorizationRequestRow, request_id)
    assert row is not None
    assert row.bound_user_id == user_id


async def test_the_consent_view_shows_the_destination_the_decision_will_use(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    port_uri = "http://localhost:51999/callback"
    request_id = await begin(server, client, redirect_uri=port_uri)
    view = await server.consent_view(
        request_id, user_id=user_id, browser_secret=BROWSERS[request_id]
    )
    assert view.redirect_uri == port_uri  # the stored request, port included
    assert view.redirect_host == "localhost"
    redirect = await server.decide(
        request_id=request_id,
        user_id=user_id,
        approve=True,
        nonce=view.nonce,
        browser_secret=BROWSERS[request_id],
    )
    assert redirect.startswith(port_uri + "?")


async def test_a_client_is_unverified_unless_the_operator_lists_it(
    db_session: AsyncSession,
) -> None:
    user_id = await make_user(db_session)
    plain = AuthorizationServer(db_session, make_settings())
    client = await register(plain)
    request_id = await begin(plain, client)
    view = await plain.consent_view(
        request_id, user_id=user_id, browser_secret=BROWSERS[request_id]
    )
    assert view.verified is False
    vouched = AuthorizationServer(
        db_session, make_settings(oauth_verified_client_ids=f"other, {client.client_id}")
    )
    request_id = await begin(vouched, client)
    view = await vouched.consent_view(
        request_id, user_id=user_id, browser_secret=BROWSERS[request_id]
    )
    assert view.verified is True


# ------------------------------------------------------------ redirect composition


@pytest.mark.parametrize(
    "presented",
    [
        "http://evil.com\\@localhost:1/callback",
        "http://localhost:1@evil.com/callback",
        "http://localhost:1/callback\n",
        "http://LOCALHOST:1/callback",
        "http://localhost:0/callback",
    ],
)
async def test_authorize_never_redirects_to_or_stores_a_tricky_uri(
    server: AuthorizationServer, db_session: AsyncSession, presented: str
) -> None:
    client = await register(server, "http://localhost/callback")
    with pytest.raises(AuthorizeRejectedError) as excinfo:
        await begin(server, client, redirect_uri=presented)
    assert excinfo.value.redirect_uri is None
    assert (await db_session.scalars(select(OAuthAuthorizationRequestRow))).all() == []


def test_adding_parameters_can_never_change_the_destination() -> None:
    base = "http://localhost:51999/callback?keep=1"
    built = build_redirect(base, code="a&b=c@evil.com/#", state="x/../y", iss="https://i")
    parts = urlsplit(built)
    assert (parts.scheme, parts.netloc, parts.path) == ("http", "localhost:51999", "/callback")
    assert parts.fragment == ""
    assert parse_qs(parts.query) == {
        "keep": ["1"],
        "code": ["a&b=c@evil.com/#"],
        "state": ["x/../y"],
        "iss": ["https://i"],
    }
    custom = build_redirect("vscode://vscode.github-authentication/did-authenticate", code="c")
    assert custom.startswith("vscode://vscode.github-authentication/did-authenticate?")


# ------------------------------------------------------------ code replay + redirect_uri


async def test_replaying_a_code_as_another_client_cannot_revoke_the_grant(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    attacker = await register(server, "https://attacker.example/cb")
    user_id = await make_user(db_session)
    code = await get_code(server, client, user_id)
    first = await redeem(server, client, code)
    error = await protocol_error(redeem(server, attacker, code))
    assert error.error == "invalid_grant"
    grant = (await db_session.scalars(select(OAuthGrantRow))).one()
    assert grant.revoked_at is None  # someone who merely saw the code cut nothing off
    assert (
        await server.refresh(
            client_id=client.client_id,
            refresh_token=first.refresh_token,
            scope=None,
            resource=None,
        )
    ).access_token


async def test_redirect_uri_is_optional_at_the_token_endpoint_when_the_request_omitted_it(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    code = await get_code(server, client, user_id, redirect_uri=None)
    assert (await redeem(server, client, code, redirect_uri=None)).access_token
    code = await get_code(server, client, user_id, redirect_uri=None)
    assert (await redeem(server, client, code)).access_token  # sending it anyway is fine
    code = await get_code(server, client, user_id, redirect_uri=None)
    error = await protocol_error(redeem(server, client, code, redirect_uri="https://evil/cb"))
    assert error.error == "invalid_grant"


async def test_redirect_uri_is_required_at_the_token_endpoint_when_the_request_had_one(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    client = await register(server)
    user_id = await make_user(db_session)
    code = await get_code(server, client, user_id)  # sent explicitly
    error = await protocol_error(redeem(server, client, code, redirect_uri=None))
    assert error.error == "invalid_grant"


# ------------------------------------------------------------ abuse bounds


class SessionProbe(FakeMetadataSource):
    """Records whether a database transaction was open while the remote fetch ran."""

    def __init__(self, session: AsyncSession, documents: dict[str, dict[str, object]]) -> None:
        super().__init__(documents)
        self.session = session
        self.in_transaction_during_fetch: list[bool] = []

    async def fetch(self, url: str) -> dict[str, object]:
        self.in_transaction_during_fetch.append(self.session.in_transaction())
        return await super().fetch(url)


async def test_no_database_transaction_is_held_open_during_the_remote_fetch(
    db_session: AsyncSession, settings: Settings
) -> None:
    probe = SessionProbe(db_session, {CIMD_URL: cimd_document()})
    server = AuthorizationServer(db_session, settings, metadata_source=probe)
    await server.resolve_client(CIMD_URL)
    assert probe.in_transaction_during_fetch == [False]


async def test_a_new_metadata_fetch_is_gated_but_a_cached_client_is_not(
    db_session: AsyncSession, settings: Settings, source: FakeMetadataSource
) -> None:
    asked: list[int] = []
    allow = [True]

    async def gate() -> bool:
        asked.append(1)
        return allow[0]

    server = AuthorizationServer(db_session, settings, metadata_source=source, fetch_gate=gate)
    allow[0] = False
    error = await protocol_error(server.resolve_client(CIMD_URL))
    assert (error.error, error.status) == ("temporarily_unavailable", 429)
    assert source.fetched == []
    allow[0] = True
    await server.resolve_client(CIMD_URL)
    assert source.fetched == [CIMD_URL]
    await server.resolve_client(CIMD_URL)  # served from the cache: no new fetch, no gate
    assert len(asked) == 2


async def test_metadata_document_clients_have_their_own_cap(
    db_session: AsyncSession, source: FakeMetadataSource
) -> None:
    capped = AuthorizationServer(
        db_session, make_settings(oauth_max_cimd_clients=1), metadata_source=source
    )
    await capped.resolve_client(CIMD_URL)
    other = "https://other.example/client.json"
    source.documents[other] = cimd_document(client_id=other)
    error = await protocol_error(capped.resolve_client(other))
    assert (error.error, error.status) == ("temporarily_unavailable", 503)
    assert (await register(capped)).kind == "dcr"  # registered clients are counted separately


async def test_unused_clients_are_dropped_after_a_few_days_not_a_month(
    db_session: AsyncSession,
) -> None:
    server = AuthorizationServer(db_session, make_settings(oauth_max_registered_clients=100))
    fresh = await register(server)
    old = await register(server)
    await db_session.execute(
        update(OAuthClientRow)
        .where(OAuthClientRow.client_id == old.client_id)
        .values(last_seen_at=datetime.now(UTC) - timedelta(days=4))
    )
    await register(server)  # registration collects the stale ones
    assert await db_session.get(OAuthClientRow, old.client_id) is None
    assert await db_session.get(OAuthClientRow, fresh.client_id) is not None


async def test_erasing_an_account_removes_the_sign_in_requests_it_opened(
    server: AuthorizationServer, db_session: AsyncSession
) -> None:
    from accounts.erasure import erase_user  # noqa: PLC0415

    client = await register(server)
    user_id = await make_user(db_session)
    request_id = await begin(server, client)
    await server.consent_view(request_id, user_id=user_id, browser_secret=BROWSERS[request_id])
    removed = await erase_user(db_session, user_id)
    assert removed["oauth_authorization_requests"] == 1
    assert await db_session.get(OAuthAuthorizationRequestRow, request_id) is None
