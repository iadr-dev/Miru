"""The whole "sign in with your Miru account" flow, driven by the OFFICIAL `mcp` SDK client.

Two real HTTP servers, each on its own thread: the API app (the authorization server) and the MCP
app (the resource server), sharing one database. The client is the SDK's own
`OAuthClientProvider`, so discovery, dynamic registration, PKCE and the `resource` parameter are
exactly what a real MCP client performs:

    initialize -> 401 + WWW-Authenticate -> protected-resource metadata -> authorization-server
    metadata -> POST /oauth/register -> GET /oauth/authorize -> (the browser: web consent screen
    calls GET/POST /api/v1/oauth/consent with the user's session cookie) -> redirect to the
    client's loopback URI with the code -> POST /oauth/token -> tools/list and tools/call

The only part played by a stand-in is the browser/web app: `redirect_handler` follows the same
redirects and makes the same two consent calls the Next.js consent screen makes. Nothing is
mocked on the server side.
"""

from __future__ import annotations

from mcp._vendor import bootstrap_vendor_sdk

bootstrap_vendor_sdk()

import socket  # noqa: E402
import time  # noqa: E402
from collections.abc import AsyncIterator, Awaitable, Callable  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from urllib.parse import parse_qs, urlsplit  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from mcp.client.auth import OAuthClientProvider  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from mcp.shared.auth import (  # noqa: E402
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from pydantic import AnyUrl  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402

from accounts.auth import issue_access_token  # noqa: E402
from accounts.store import SqlAlchemyUserStore  # noqa: E402
from config.settings import Settings  # noqa: E402
from mcp.composition import build_mcp_http_app  # noqa: E402
from schemas.enums import Tier  # noqa: E402
from tests.api.conftest import _TEST_JWT_SECRET, _insert_user, build_test_app  # noqa: E402
from tests.mcp.conftest import build_fake_registry  # noqa: E402
from tests.mcp.live_server import serve  # noqa: E402

CALLBACK = "http://localhost:3030/callback"
SPONSOR_CALL = {"id": "fake_sponsor_tool", "args": {"value": 3}}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class MemoryTokenStorage:
    """The SDK's `TokenStorage` protocol, in memory."""

    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


@dataclass
class Deployment:
    api_url: str
    mcp_url: str
    settings: Settings
    session_factory: async_sessionmaker  # type: ignore[type-arg]
    consent_requests: list[str]

    async def new_user(self, tier: Tier) -> str:
        return await _insert_user(self.session_factory, tier=tier)

    async def set_tier(self, user_id: str, tier: Tier) -> None:
        async with self.session_factory() as session:
            await SqlAlchemyUserStore(session).set_tier(user_id=user_id, tier=tier)
            await session.commit()

    def browser_for(self, user_id: str) -> httpx.AsyncClient:
        """A browser with `user_id` signed in to the web app: it holds the session cookie."""
        browser = httpx.AsyncClient(follow_redirects=False, timeout=30)
        token = issue_access_token(user_id=user_id, settings=self.settings)
        browser.cookies.set("access_token", token, domain="127.0.0.1")
        return browser

    def sign_in_handlers(
        self, browser: httpx.AsyncClient, *, approve: bool = True
    ) -> tuple[Callable[[str], Awaitable[None]], Callable[[], Awaitable[tuple[str, str | None]]]]:
        landed: dict[str, str | None] = {}

        async def redirect_handler(authorization_url: str) -> None:
            # 1. The system browser opens the authorization URL.
            first = await browser.get(authorization_url)
            assert first.status_code == 302, first.text
            consent_page = first.headers["location"]
            assert "/oauth/consent?request=" in consent_page  # the web app's consent screen
            request_id = parse_qs(urlsplit(consent_page).query)["request"][0]
            self.consent_requests.append(request_id)
            # 2. The consent screen reads the request, then posts the user's decision.
            shown = await browser.get(f"{self.api_url}/api/v1/oauth/consent/{request_id}")
            assert shown.status_code == 200, shown.text
            decided = await browser.post(
                f"{self.api_url}/api/v1/oauth/consent/{request_id}",
                json={
                    "decision": "approve" if approve else "deny",
                    "nonce": shown.json()["nonce"],
                },
            )
            assert decided.status_code == 200, decided.text
            # 3. The page navigates the browser to the client's redirect URI.
            target = urlsplit(decided.json()["redirect_to"])
            assert f"{target.scheme}://{target.netloc}{target.path}" == CALLBACK
            params = parse_qs(target.query)
            assert params["iss"] == [self.api_url]  # RFC 9207
            landed["code"] = params.get("code", [None])[0]
            landed["state"] = params.get("state", [None])[0]
            landed["error"] = params.get("error", [None])[0]

        async def callback_handler() -> tuple[str, str | None]:
            if landed.get("error"):
                raise RuntimeError(f"authorization failed: {landed['error']}")
            code = landed["code"]
            assert code is not None
            return code, landed["state"]

        return redirect_handler, callback_handler

    def provider(
        self,
        storage: MemoryTokenStorage,
        browser: httpx.AsyncClient,
        *,
        approve: bool = True,
    ) -> OAuthClientProvider:
        redirect_handler, callback_handler = self.sign_in_handlers(browser, approve=approve)
        return OAuthClientProvider(
            server_url=self.mcp_url,
            client_metadata=OAuthClientMetadata(
                client_name="E2E MCP client",
                redirect_uris=[AnyUrl(CALLBACK)],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",
                scope="mcp:read",
            ),
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            timeout=30,
        )


@pytest.fixture
async def deployment() -> AsyncIterator[Deployment]:
    api_port, mcp_port = _free_port(), _free_port()
    api_url, mcp_base = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{mcp_port}"
    settings = Settings(
        jwt_secret_key=_TEST_JWT_SECRET,
        env="development",
        oauth_issuer=api_url,
        mcp_public_base_url=mcp_base,
        frontend_base_url=api_url,
        voice_stt_provider="stub",
        voice_tts_provider="stub",
        _env_file=None,  # type: ignore[call-arg]
    )
    api_app = build_test_app(settings, agent_answer="x")
    state = api_app.state.app_state
    database_url = state.session_factory.kw["bind"].url.render_as_string(hide_password=False)
    mcp_settings = settings.model_copy(update={"database_url": database_url})
    built = build_mcp_http_app(mcp_settings, registry=build_fake_registry())
    async with serve(api_app, port=api_port), serve(built.app, port=mcp_port):
        yield Deployment(
            api_url=api_url,
            mcp_url=f"{mcp_base}/mcp",
            settings=settings,
            session_factory=state.session_factory,
            consent_requests=[],
        )


async def _call_sponsor_tool(session: ClientSession) -> dict[str, object]:
    result = await session.call_tool("execute_tool", SPONSOR_CALL)
    assert not result.isError
    assert result.structuredContent is not None
    return result.structuredContent


async def _connect(
    deployment: Deployment, provider: OAuthClientProvider
) -> tuple[list[str], dict[str, object]]:
    async with (
        httpx.AsyncClient(auth=provider, timeout=30, follow_redirects=True) as http,
        streamable_http_client(deployment.mcp_url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = [t.name for t in (await session.list_tools()).tools]
        return tools, await _call_sponsor_tool(session)


async def test_a_premium_user_signs_in_with_the_sdk_client_and_gets_paid_data(
    deployment: Deployment,
) -> None:
    user_id = await deployment.new_user(Tier.PREMIUM)
    storage = MemoryTokenStorage()
    async with deployment.browser_for(user_id) as browser:
        tools, data = await _connect(deployment, deployment.provider(storage, browser))

    assert "search_tools" in tools
    assert "execute_tool" in tools
    assert data == {"doubled": 6}  # Premium sees the sponsor data unmasked
    # The client registered itself, and received tokens for this resource.
    assert storage.client_info is not None
    assert storage.client_info.client_id
    assert storage.tokens is not None
    assert storage.tokens.token_type.lower() == "bearer"
    assert storage.tokens.refresh_token
    assert storage.tokens.scope == "mcp:read"
    assert len(deployment.consent_requests) == 1


async def test_a_free_user_signs_in_too_and_gets_the_public_teaser(
    deployment: Deployment,
) -> None:
    user_id = await deployment.new_user(Tier.FREE)
    async with deployment.browser_for(user_id) as browser:
        _, data = await _connect(deployment, deployment.provider(MemoryTokenStorage(), browser))
    assert data["locked"] is True


async def test_a_tier_change_applies_to_the_same_signed_in_client_on_its_next_request(
    deployment: Deployment,
) -> None:
    user_id = await deployment.new_user(Tier.FREE)
    storage = MemoryTokenStorage()
    async with (
        deployment.browser_for(user_id) as browser,
        httpx.AsyncClient(
            auth=deployment.provider(storage, browser), timeout=30, follow_redirects=True
        ) as http,
        streamable_http_client(deployment.mcp_url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        assert (await _call_sponsor_tool(session))["locked"] is True
        await deployment.set_tier(user_id, Tier.PRO)
        assert await _call_sponsor_tool(session) == {"doubled": 6}
        await deployment.set_tier(user_id, Tier.FREE)
        assert (await _call_sponsor_tool(session))["locked"] is True
    assert len(deployment.consent_requests) == 1  # one sign-in served all three calls


async def test_an_expired_access_token_is_refreshed_by_the_sdk_and_the_refresh_token_rotates(
    deployment: Deployment,
) -> None:
    user_id = await deployment.new_user(Tier.PREMIUM)
    storage = MemoryTokenStorage()
    provider = deployment.provider(storage, deployment.browser_for(user_id))
    async with (
        httpx.AsyncClient(auth=provider, timeout=30, follow_redirects=True) as http,
        streamable_http_client(deployment.mcp_url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        assert await _call_sponsor_tool(session) == {"doubled": 6}
        before = storage.tokens
        assert before is not None
        provider.context.token_expiry_time = time.time() - 10  # the access token has "expired"
        assert await _call_sponsor_tool(session) == {"doubled": 6}
        after = storage.tokens
    assert after is not None
    assert after.access_token != before.access_token
    assert after.refresh_token != before.refresh_token  # rotated
    assert len(deployment.consent_requests) == 1  # no second consent screen: it was a refresh


async def test_without_signing_in_the_sdk_client_cannot_reach_any_tool(
    deployment: Deployment,
) -> None:
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            deployment.mcp_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
    assert resp.status_code == 401
    challenge = resp.headers["www-authenticate"]
    metadata_url = challenge.split('resource_metadata="')[1].split('"')[0]
    async with httpx.AsyncClient() as http:
        prm = (await http.get(metadata_url)).json()
        assert prm["resource"] == deployment.mcp_url
        assert prm["authorization_servers"] == [deployment.api_url]
        as_meta = (
            await http.get(
                f"{prm['authorization_servers'][0]}/.well-known/oauth-authorization-server"
            )
        ).json()
    assert as_meta["issuer"] == deployment.api_url
    assert as_meta["code_challenge_methods_supported"] == ["S256"]


async def test_denying_consent_gives_the_client_no_tokens(deployment: Deployment) -> None:
    user_id = await deployment.new_user(Tier.PREMIUM)
    storage = MemoryTokenStorage()
    async with deployment.browser_for(user_id) as browser:
        provider = deployment.provider(storage, browser, approve=False)
        with pytest.raises(BaseException) as excinfo:
            await _connect(deployment, provider)
    assert "access_denied" in repr(excinfo.value)
    assert storage.tokens is None


async def test_revoking_with_rfc_7009_stops_the_access_token_at_once(
    deployment: Deployment,
) -> None:
    user_id = await deployment.new_user(Tier.PREMIUM)
    storage = MemoryTokenStorage()
    async with deployment.browser_for(user_id) as browser:
        await _connect(deployment, deployment.provider(storage, browser))
    assert storage.tokens is not None
    assert storage.client_info is not None
    headers = {
        "authorization": f"Bearer {storage.tokens.access_token}",
        "accept": "application/json, text/event-stream",
    }
    probe = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    async with httpx.AsyncClient() as http:
        assert (await http.post(deployment.mcp_url, json=probe, headers=headers)).status_code == 200
        revoked = await http.post(
            f"{deployment.api_url}/oauth/revoke",
            data={
                "token": storage.tokens.refresh_token,
                "client_id": storage.client_info.client_id,
            },
        )
        assert revoked.status_code == 200
        after = await http.post(deployment.mcp_url, json=probe, headers=headers)
    assert after.status_code == 401
    assert 'error="invalid_token"' in after.headers["www-authenticate"]
