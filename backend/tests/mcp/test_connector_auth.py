"""Sign-in at the MCP edge, end to end: a real user row with a real tier in a SQLite database, a
real OAuth grant, access tokens signed by `accounts.mcp_tokens`, the real gateway and resolver,
driven over HTTP by the official client.

- require-auth (the default): no / bad / expired / wrong-audience credential -> 401 + the
  RFC 9728 challenge that starts a client's sign-in flow
- a Free user and a Premium user calling the same sponsor-data tool get a masked teaser and the
  data respectively; a tier change applies on the very next request (the token carries no tier)
- the web app's own session JWT is NOT a credential here unless the dev flag is on
- require-auth off (local development): the old anonymous Free behaviour
"""

from __future__ import annotations

from mcp._vendor import bootstrap_vendor_sdk

bootstrap_vendor_sdk()

import time  # noqa: E402
import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import update  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from accounts.auth import issue_access_token  # noqa: E402
from accounts.mcp_tokens import issue_mcp_access_token  # noqa: E402
from accounts.models import OAuthProvider  # noqa: E402
from accounts.store import SqlAlchemyUserStore  # noqa: E402
from config.settings import Settings  # noqa: E402
from db import models  # noqa: E402, F401  # pyright: ignore[reportUnusedImport]
from db.base import Base  # noqa: E402
from db.models import OAuthClientRow, OAuthGrantRow  # noqa: E402
from mcp.accounts_resolver import AccountsPrincipalResolver  # noqa: E402
from mcp.auth import McpTokenVerifier  # noqa: E402
from schemas.enums import Tier  # noqa: E402
from tests.mcp.conftest import build_fake_registry  # noqa: E402
from tests.mcp.connector_support import (  # noqa: E402
    INITIALIZE,
    MCP_HEADERS,
    build_app,
    fastmcp_client,
    official_session,
)
from tests.mcp.live_server import serve  # noqa: E402

SECRET = "test-secret-not-a-real-key-0123456789abcdef"
ISSUER = "https://api.example.com"
RESOURCE = "https://mcp.example.com/mcp"
METADATA_URL = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
SPONSOR_CALL = {"id": "fake_sponsor_tool", "args": {"value": 3}}


def _settings(**overrides: object) -> Settings:
    return Settings(
        jwt_secret_key=SECRET,
        oauth_issuer=ISSUER,
        mcp_public_base_url="https://mcp.example.com",
        _env_file=None,  # type: ignore[call-arg]
        **overrides,  # type: ignore[arg-type]
    )


@pytest.fixture
def settings() -> Settings:
    return _settings()


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mcp_auth.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_user(factory: async_sessionmaker[AsyncSession], *, tier: Tier, account: str) -> str:
    async with factory() as session:
        store = SqlAlchemyUserStore(session)
        user = await store.upsert_via_oauth(
            provider=OAuthProvider.GOOGLE,
            provider_account_id=account,
            email=f"{account}@example.com",
            email_verified=True,
        )
        if tier is not Tier.FREE:
            user = await store.set_tier(user_id=user.id, tier=tier)
        await session.commit()
        return user.id


async def _grant(factory: async_sessionmaker[AsyncSession], user_id: str) -> str:
    """A standing consent for `user_id`, as the authorization server would have recorded."""
    now = datetime.now(UTC)
    client_id = str(uuid.uuid4())
    grant_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(
            OAuthClientRow(
                client_id=client_id,
                name="Test client",
                redirect_uris=["http://localhost/cb"],
                kind="dcr",
                token_endpoint_auth_method="none",
                created_at=now,
                last_seen_at=now,
            )
        )
        await session.flush()
        session.add(
            OAuthGrantRow(
                id=grant_id,
                user_id=user_id,
                client_id=client_id,
                scope="mcp:read",
                resource=RESOURCE,
                created_at=now,
            )
        )
        await session.commit()
    return grant_id


def _token(settings: Settings, user_id: str, grant_id: str, **overrides: str) -> str:
    claims = {
        "user_id": user_id,
        "client_id": "client-1",
        "grant_id": grant_id,
        "scope": "mcp:read",
        "issuer": ISSUER,
        "resource": RESOURCE,
        **overrides,
    }
    token, _ = issue_mcp_access_token(settings=settings, **claims)  # type: ignore[arg-type]
    return token


async def _signed_in_token(
    factory: async_sessionmaker[AsyncSession], settings: Settings, *, tier: Tier, account: str
) -> tuple[str, str, str]:
    user_id = await _make_user(factory, tier=tier, account=account)
    grant_id = await _grant(factory, user_id)
    return _token(settings, user_id, grant_id), user_id, grant_id


def _app(settings: Settings, factory: async_sessionmaker[AsyncSession], **kwargs: object):
    verifier = McpTokenVerifier(settings=settings, session_factory=factory)
    resolver = AccountsPrincipalResolver(
        settings=settings, session_factory=factory, verifier=verifier
    )
    return build_app(
        build_fake_registry(),
        verify_token=verifier.verify,
        principal_resolver=resolver,
        bearer_resolver=resolver,
        resource_metadata_url=METADATA_URL,
        require_auth=settings.mcp_require_auth,
        **kwargs,  # type: ignore[arg-type]
    )


async def _post(server_url: str, headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient() as http:
        return await http.post(server_url, json=INITIALIZE, headers={**MCP_HEADERS, **headers})


# ----------------------------------------------------------------------- the 401 challenge


async def test_no_credential_gets_the_challenge_that_starts_sign_in(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with serve(_app(settings, session_factory)) as server:
        resp = await _post(server.mcp_url, {})
    assert resp.status_code == 401
    challenge = resp.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{METADATA_URL}"' in challenge
    assert 'scope="mcp:read"' in challenge
    assert "error=" not in challenge  # RFC 6750: no error code when no credential was presented
    assert resp.headers["cache-control"] == "no-store"


async def test_a_get_stream_request_without_a_credential_is_challenged_too(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with serve(_app(settings, session_factory)) as server, httpx.AsyncClient() as http:
        resp = await http.get(server.mcp_url, headers={"accept": "text/event-stream"})
    assert resp.status_code == 401
    assert "resource_metadata" in resp.headers["www-authenticate"]


async def test_invalid_expired_and_misdirected_tokens_are_each_refused_with_invalid_token(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await _make_user(session_factory, tier=Tier.PREMIUM, account="premium-2")
    grant_id = await _grant(session_factory, user_id)
    good = _token(settings, user_id, grant_id)
    expired = jwt.decode(good, options={"verify_signature": False})
    expired.update(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)
    from accounts.mcp_tokens import _signing_key  # noqa: PLC0415

    key = _signing_key(settings)
    cases = {
        "expired": jwt.encode(expired, key, algorithm="HS256"),
        "forged": jwt.encode({**expired, "exp": int(time.time()) + 600}, "other-key" * 5, "HS256"),
        "other-audience": _token(settings, user_id, grant_id, resource="https://evil.example/mcp"),
        "other-issuer": _token(settings, user_id, grant_id, issuer="https://evil.example"),
        "garbage": "garbage",
    }
    async with serve(_app(settings, session_factory)) as server:
        for name, token in cases.items():
            resp = await _post(server.mcp_url, {"authorization": f"Bearer {token}"})
            assert resp.status_code == 401, name
            challenge = resp.headers["www-authenticate"]
            assert 'error="invalid_token"' in challenge, name
            assert f'resource_metadata="{METADATA_URL}"' in challenge, name
            assert ("expired" in challenge) is (name == "expired"), name
        scheme = await _post(server.mcp_url, {"authorization": "Basic dXNlcjpwYXNz"})
        assert scheme.status_code == 401


async def test_the_apps_own_session_jwt_is_not_an_mcp_credential(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await _make_user(session_factory, tier=Tier.PREMIUM, account="premium-app")
    session_jwt = issue_access_token(user_id=user_id, settings=settings)
    async with serve(_app(settings, session_factory)) as server:
        for headers in (
            {"authorization": f"Bearer {session_jwt}"},
            {"x-api-key": session_jwt},
        ):
            resp = await _post(server.mcp_url, headers)
            assert resp.status_code == 401
            assert 'error="invalid_token"' in resp.headers["www-authenticate"]


async def test_the_dev_flag_lets_the_session_jwt_through(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    dev = _settings(mcp_accept_app_access_token=True)
    user_id = await _make_user(session_factory, tier=Tier.PREMIUM, account="premium-dev")
    session_jwt = issue_access_token(user_id=user_id, settings=dev)
    async with (
        serve(_app(dev, session_factory)) as server,
        official_session(server, headers={"Authorization": f"Bearer {session_jwt}"}) as s,
    ):
        result = await s.call_tool("execute_tool", SPONSOR_CALL)
        assert result.structuredContent == {"doubled": 6}


async def test_a_revoked_grant_stops_its_access_token_at_once(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    token, _, grant_id = await _signed_in_token(
        session_factory, settings, tier=Tier.PREMIUM, account="premium-rev"
    )
    headers = {"authorization": f"Bearer {token}"}
    async with serve(_app(settings, session_factory)) as server:
        assert (await _post(server.mcp_url, headers)).status_code == 200
        async with session_factory() as session:
            await session.execute(
                update(OAuthGrantRow)
                .where(OAuthGrantRow.id == grant_id)
                .values(revoked_at=datetime.now(UTC))
            )
            await session.commit()
        assert (await _post(server.mcp_url, headers)).status_code == 401


async def test_open_paths_need_no_credential(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with serve(_app(settings, session_factory)) as server, httpx.AsyncClient() as http:
        assert (await http.get(server.base_url + "/healthz")).status_code == 200
        preflight = await http.request(
            "OPTIONS",
            server.mcp_url,
            headers={"origin": "https://claude.ai", "access-control-request-method": "POST"},
        )
        assert preflight.status_code != 401


# ----------------------------------------------------------------------- tiers


async def test_free_and_premium_users_get_masked_and_unmasked_data_from_the_same_tool(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    free, _, _ = await _signed_in_token(session_factory, settings, tier=Tier.FREE, account="free-1")
    premium, _, _ = await _signed_in_token(
        session_factory, settings, tier=Tier.PREMIUM, account="premium-1"
    )
    async with serve(_app(settings, session_factory)) as server:
        async with official_session(
            server, headers={"Authorization": f"Bearer {free}"}
        ) as free_session:
            masked = await free_session.call_tool("execute_tool", SPONSOR_CALL)
            assert masked.structuredContent["locked"] is True  # type: ignore[index]
        async with official_session(
            server, headers={"Authorization": f"Bearer {premium}"}
        ) as premium_session:
            data = await premium_session.call_tool("execute_tool", SPONSOR_CALL)
            assert data.structuredContent == {"doubled": 6}
        async with fastmcp_client(server, headers={"Authorization": f"Bearer {premium}"}) as client:
            via_fastmcp = await client.call_tool("execute_tool", SPONSOR_CALL)
            assert via_fastmcp.structured_content == {"doubled": 6}


async def test_a_tier_change_applies_on_the_next_request_with_the_same_token(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    token, user_id, _ = await _signed_in_token(
        session_factory, settings, tier=Tier.FREE, account="upgrader"
    )
    async with (
        serve(_app(settings, session_factory)) as server,
        official_session(server, headers={"Authorization": f"Bearer {token}"}) as s,
    ):
        assert (await s.call_tool("execute_tool", SPONSOR_CALL)).structuredContent[  # type: ignore[index]
            "locked"
        ] is True
        async with session_factory() as session:
            await SqlAlchemyUserStore(session).set_tier(user_id=user_id, tier=Tier.PRO)
            await session.commit()
        assert (await s.call_tool("execute_tool", SPONSOR_CALL)).structuredContent == {"doubled": 6}
        async with session_factory() as session:
            await SqlAlchemyUserStore(session).set_tier(user_id=user_id, tier=Tier.FREE)
            await session.commit()
        assert (await s.call_tool("execute_tool", SPONSOR_CALL)).structuredContent[  # type: ignore[index]
            "locked"
        ] is True


def test_the_access_token_carries_no_tier_or_entitlement(settings: Settings) -> None:
    token = _token(settings, "user-1", "grant-1")
    claims = jwt.decode(token, options={"verify_signature": False})
    assert set(claims) == {
        "typ", "iss", "aud", "sub", "scope", "client_id", "gid", "iat", "exp", "jti",
    }  # fmt: skip
    assert claims["typ"] == "mcp_access"
    assert claims["aud"] == RESOURCE


async def test_x_api_key_carries_the_same_token_to_the_same_tier(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    token, _, _ = await _signed_in_token(session_factory, settings, tier=Tier.PRO, account="pro-1")
    async with (
        serve(_app(settings, session_factory)) as server,
        official_session(server, headers={"x-api-key": token}) as s,
    ):
        result = await s.call_tool("execute_tool", SPONSOR_CALL)
        assert result.structuredContent == {"doubled": 6}


async def test_a_bad_x_api_key_is_refused_when_sign_in_is_required(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with serve(_app(settings, session_factory)) as server:
        resp = await _post(server.mcp_url, {"x-api-key": "not-a-token"})
    assert resp.status_code == 401


async def test_official_client_surfaces_a_401_instead_of_hanging(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with serve(_app(settings, session_factory)) as server:
        session = official_session(server)
        with pytest.raises(BaseException) as excinfo:
            async with session:
                pytest.fail("a 401 must not yield a usable session")
        assert "401" in repr(excinfo.value)


async def test_signed_in_users_are_rate_limited_separately_from_anonymous_callers(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    dev = _settings(mcp_require_auth=False)
    token, _, _ = await _signed_in_token(
        session_factory, dev, tier=Tier.PREMIUM, account="premium-3"
    )
    app = _app(dev, session_factory, rate_anonymous_per_minute=1, rate_signed_in_per_minute=3)
    bearer = {"authorization": f"Bearer {token}"}
    async with serve(app) as server:
        codes = [(await _post(server.mcp_url, bearer)).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        # The same address, anonymously, has its own (smaller) allowance.
        assert (await _post(server.mcp_url, {})).status_code == 200


# ----------------------------------------------------------------------- require-auth off (dev)


async def test_with_require_auth_off_an_anonymous_caller_is_free_and_masked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    dev = _settings(mcp_require_auth=False)
    async with serve(_app(dev, session_factory)) as server, official_session(server) as s:
        result = await s.call_tool("execute_tool", SPONSOR_CALL)
        assert result.structuredContent["locked"] is True  # type: ignore[index]


async def test_with_require_auth_off_a_presented_bad_bearer_is_still_a_401(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    dev = _settings(mcp_require_auth=False)
    async with serve(_app(dev, session_factory)) as server:
        resp = await _post(server.mcp_url, {"authorization": "Bearer nope"})
    assert resp.status_code == 401


async def test_without_a_signing_secret_nothing_authenticates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    unconfigured = Settings(
        jwt_secret_key=None,
        oauth_issuer=ISSUER,
        mcp_public_base_url="https://mcp.example.com",
        _env_file=None,  # type: ignore[call-arg]
    )
    verdict = await McpTokenVerifier(settings=unconfigured, session_factory=session_factory).verify(
        "anything"
    )
    assert verdict.user_id is None
