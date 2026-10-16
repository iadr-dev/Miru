"""Auth paths for Claude connectors, end to end: the app's real signed access JWT (issued by
`accounts.auth`), a real user row with a real tier in a SQLite database, the real gateway and
resolver, driven over HTTP by the official client.

- no credential           -> anonymous, FREE (the authless connector): sponsor data is masked
- Bearer / x-api-key JWT  -> that user's tier: sponsor data unmasked
- bad / expired Bearer    -> 401 + WWW-Authenticate (resource_metadata when configured)
"""

from __future__ import annotations

from mcp._vendor import bootstrap_vendor_sdk

bootstrap_vendor_sdk()

import time  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from accounts.auth import issue_access_token  # noqa: E402
from accounts.models import OAuthProvider  # noqa: E402
from accounts.store import SqlAlchemyUserStore  # noqa: E402
from config.settings import Settings  # noqa: E402
from db import models  # noqa: E402, F401  # pyright: ignore[reportUnusedImport]
from db.base import Base  # noqa: E402
from mcp.accounts_resolver import AccountsPrincipalResolver, verify_access_token  # noqa: E402
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
METADATA_URL = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
SPONSOR_CALL = {"id": "fake_sponsor_tool", "args": {"value": 3}}


@pytest.fixture
def settings() -> Settings:
    return Settings(jwt_secret_key=SECRET, _env_file=None)  # type: ignore[call-arg]


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


def _app(settings: Settings, factory: async_sessionmaker[AsyncSession], **kwargs: object):
    resolver = AccountsPrincipalResolver(settings=settings, session_factory=factory)
    return build_app(
        build_fake_registry(),
        verify_token=lambda token: verify_access_token(token, settings),
        principal_resolver=resolver,
        bearer_resolver=resolver,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_anonymous_stays_free_and_sponsor_data_is_masked(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with serve(_app(settings, session_factory)) as server, official_session(server) as s:
        result = await s.call_tool("execute_tool", SPONSOR_CALL)
        assert result.structuredContent["locked"] is True  # type: ignore[index]


async def test_premium_bearer_token_unmasks_sponsor_data(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await _make_user(session_factory, tier=Tier.PREMIUM, account="premium-1")
    token = issue_access_token(user_id=user_id, settings=settings)
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        serve(_app(settings, session_factory)) as server,
        official_session(server, headers=headers) as s,
    ):
        result = await s.call_tool("execute_tool", SPONSOR_CALL)
        assert result.structuredContent == {"doubled": 6}
        async with fastmcp_client(server, headers=headers) as client:
            via_fastmcp = await client.call_tool("execute_tool", SPONSOR_CALL)
            assert via_fastmcp.structured_content == {"doubled": 6}


async def test_free_user_bearer_token_is_still_masked(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await _make_user(session_factory, tier=Tier.FREE, account="free-1")
    token = issue_access_token(user_id=user_id, settings=settings)
    async with (
        serve(_app(settings, session_factory)) as server,
        official_session(server, headers={"Authorization": f"Bearer {token}"}) as s,
    ):
        result = await s.call_tool("execute_tool", SPONSOR_CALL)
        assert result.structuredContent["locked"] is True  # type: ignore[index]


async def test_x_api_key_header_carries_the_same_token_to_the_same_tier(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await _make_user(session_factory, tier=Tier.PRO, account="pro-1")
    token = issue_access_token(user_id=user_id, settings=settings)
    async with (
        serve(_app(settings, session_factory)) as server,
        official_session(server, headers={"x-api-key": token}) as s,
    ):
        result = await s.call_tool("execute_tool", SPONSOR_CALL)
        assert result.structuredContent == {"doubled": 6}


async def test_unknown_x_api_key_degrades_to_anonymous_not_an_error(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with (
        serve(_app(settings, session_factory)) as server,
        official_session(server, headers={"x-api-key": "not-a-token"}) as s,
    ):
        result = await s.call_tool("execute_tool", SPONSOR_CALL)
        assert result.structuredContent["locked"] is True  # type: ignore[index]


def _expired_token(user_id: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
            "typ": "access",
        },
        SECRET,
        algorithm="HS256",
    )


async def test_expired_bad_or_wrong_scheme_credentials_get_401_with_a_discovery_challenge(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await _make_user(session_factory, tier=Tier.PREMIUM, account="premium-2")
    forged = jwt.encode(
        {"sub": user_id, "exp": int(time.time()) + 600, "typ": "access"},
        "some-other-key-0123456789abcdef0123456789",
        algorithm="HS256",
    )
    refresh_like = jwt.encode(
        {"sub": user_id, "exp": int(time.time()) + 600, "typ": "ticket"}, SECRET, algorithm="HS256"
    )
    app = _app(settings, session_factory, resource_metadata_url=METADATA_URL)
    async with serve(app) as server, httpx.AsyncClient() as http:
        for header in (
            f"Bearer {_expired_token(user_id)}",
            f"Bearer {forged}",
            f"Bearer {refresh_like}",
            "Bearer garbage",
            "Basic dXNlcjpwYXNz",
        ):
            resp = await http.post(
                server.mcp_url, json=INITIALIZE, headers={**MCP_HEADERS, "authorization": header}
            )
            assert resp.status_code == 401, header
            challenge = resp.headers["www-authenticate"]
            assert challenge.startswith("Bearer ")
            assert 'error="invalid_token"' in challenge
            assert f'resource_metadata="{METADATA_URL}"' in challenge


async def test_challenge_omits_resource_metadata_when_no_authorization_server_is_configured(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with serve(_app(settings, session_factory)) as server, httpx.AsyncClient() as http:
        resp = await http.post(
            server.mcp_url, json=INITIALIZE, headers={**MCP_HEADERS, "authorization": "Bearer x"}
        )
        assert resp.status_code == 401
        assert "resource_metadata" not in resp.headers["www-authenticate"]


async def test_official_client_surfaces_a_401_instead_of_hanging(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with serve(_app(settings, session_factory)) as server:
        with pytest.raises(BaseException) as excinfo:
            async with official_session(server, headers={"Authorization": "Bearer nope"}):
                pytest.fail("a 401 must not yield a usable session")
        assert "401" in repr(excinfo.value)


async def test_signed_in_users_are_rate_limited_separately_from_anonymous_callers(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await _make_user(session_factory, tier=Tier.PREMIUM, account="premium-3")
    token = issue_access_token(user_id=user_id, settings=settings)
    app = _app(settings, session_factory, rate_anonymous_per_minute=1, rate_signed_in_per_minute=3)
    bearer = {**MCP_HEADERS, "authorization": f"Bearer {token}"}
    async with serve(app) as server, httpx.AsyncClient() as http:
        user_codes = [
            (await http.post(server.mcp_url, json=INITIALIZE, headers=bearer)).status_code
            for _ in range(4)
        ]
        assert user_codes == [200, 200, 200, 429]
        # The same address, anonymously, has its own (smaller) allowance.
        anon = (await http.post(server.mcp_url, json=INITIALIZE, headers=MCP_HEADERS)).status_code
        assert anon == 200


async def test_without_a_signing_secret_no_bearer_token_authenticates() -> None:
    unconfigured = Settings(jwt_secret_key=None, _env_file=None)  # type: ignore[call-arg]
    assert verify_access_token("anything", unconfigured) is None
