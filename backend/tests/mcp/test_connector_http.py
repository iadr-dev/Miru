"""The HTTP-level behaviors Anthropic's connectors depend on, checked with raw `httpx` against
the real app on a real socket: protocol-version negotiation, trailing slash, public Host,
discovery metadata, rate / size / concurrency limits and /healthz."""

from __future__ import annotations

from mcp._vendor import bootstrap_vendor_sdk

bootstrap_vendor_sdk()

import asyncio  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from config.settings import Settings  # noqa: E402
from mcp.discovery import resource_metadata_from_settings  # noqa: E402
from tests.mcp.conftest import build_fake_registry  # noqa: E402
from tests.mcp.connector_support import (  # noqa: E402
    INITIALIZE,
    MCP_HEADERS,
    SlowTool,
    build_app,
    official_session,
)
from tests.mcp.live_server import serve  # noqa: E402


def _initialize_body(version: str) -> dict[str, object]:
    body = json.loads(json.dumps(INITIALIZE))
    body["params"]["protocolVersion"] = version
    return body


def _json_rpc(text: str) -> dict[str, object]:
    """Body of a POST that answers as JSON or as one SSE `data:` event."""
    if text.lstrip().startswith("{"):
        return json.loads(text)
    data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    return json.loads(data[0])


@pytest.mark.parametrize("version", ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"])
async def test_every_protocol_version_a_claude_client_may_send_initializes(version: str) -> None:
    async with serve(build_app(build_fake_registry())) as server, httpx.AsyncClient() as http:
        resp = await http.post(server.mcp_url, json=_initialize_body(version), headers=MCP_HEADERS)
        assert resp.status_code == 200
        assert _json_rpc(resp.text)["result"]["protocolVersion"] == version  # type: ignore[index]


async def test_unknown_protocol_version_gets_the_servers_latest_not_an_error() -> None:
    async with serve(build_app(build_fake_registry())) as server, httpx.AsyncClient() as http:
        resp = await http.post(
            server.mcp_url, json=_initialize_body("1999-01-01"), headers=MCP_HEADERS
        )
        assert resp.status_code == 200
        assert _json_rpc(resp.text)["result"]["protocolVersion"] == "2025-11-25"  # type: ignore[index]


async def test_stateless_server_issues_no_session_id_and_accepts_older_version_header() -> None:
    async with serve(build_app(build_fake_registry())) as server, httpx.AsyncClient() as http:
        init = await http.post(server.mcp_url, json=INITIALIZE, headers=MCP_HEADERS)
        assert "mcp-session-id" not in init.headers
        # A fresh connection with no session and an older negotiated version works (this is
        # exactly the Messages API connector's one-request-per-connection pattern).
        listing = await http.post(
            server.mcp_url,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={**MCP_HEADERS, "mcp-protocol-version": "2025-03-26"},
        )
        assert listing.status_code == 200
        assert _json_rpc(listing.text)["result"]["tools"]  # type: ignore[index]


async def test_unsupported_mcp_protocol_version_header_is_a_400() -> None:
    async with serve(build_app(build_fake_registry())) as server, httpx.AsyncClient() as http:
        resp = await http.post(
            server.mcp_url,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={**MCP_HEADERS, "mcp-protocol-version": "1999-01-01"},
        )
        assert resp.status_code == 400


async def test_trailing_slash_is_served_in_place_not_redirected() -> None:
    """Starlette's own answer is a 307 to an http:// URL, which a client behind a TLS-terminating
    proxy follows to nowhere. Both forms must answer directly."""
    async with serve(build_app(build_fake_registry())) as server:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            for url in (server.mcp_url, server.mcp_url + "/"):
                resp = await http.post(url, json=INITIALIZE, headers=MCP_HEADERS)
                assert resp.status_code == 200, url
        async with official_session(server, url=server.mcp_url + "/") as session:
            assert (await session.list_tools()).tools


async def test_public_host_is_served_when_allow_listed() -> None:
    app = build_app(build_fake_registry(), allowed_hosts=["mcp.example.com"])
    async with serve(app) as server, httpx.AsyncClient() as http:
        resp = await http.post(
            server.mcp_url, json=INITIALIZE, headers={**MCP_HEADERS, "host": "mcp.example.com"}
        )
        assert resp.status_code == 200
        # An Origin the server did not allow is refused...
        bad = await http.post(
            server.mcp_url,
            json=INITIALIZE,
            headers={**MCP_HEADERS, "host": "mcp.example.com", "origin": "https://evil.test"},
        )
        assert bad.status_code == 403
        assert "MCP_ALLOWED_ORIGINS" in bad.json()["message"]


async def test_unlisted_host_is_refused_with_a_message_naming_the_setting() -> None:
    async with serve(build_app(build_fake_registry())) as server, httpx.AsyncClient() as http:
        resp = await http.post(
            server.mcp_url, json=INITIALIZE, headers={**MCP_HEADERS, "host": "mcp.example.com"}
        )
        assert resp.status_code == 421
        body = resp.json()
        assert body["error"] == "host_not_allowed"
        assert "MCP_PUBLIC_BASE_URL" in body["message"]
        assert "MCP_ALLOWED_HOSTS" in body["message"]
        assert "mcp.example.com" in body["message"]


def test_public_base_url_setting_allow_lists_its_host_and_origin() -> None:
    settings = Settings(mcp_public_base_url="https://mcp.example.com/", jwt_secret_key="x" * 40)
    assert settings.mcp_allowed_hosts_list() == ["mcp.example.com"]
    assert settings.mcp_allowed_origins_list() == ["https://mcp.example.com"]
    plain = Settings(jwt_secret_key="x" * 40)
    assert plain.mcp_allowed_hosts_list() is None


async def test_protected_resource_metadata_is_served_only_when_configured() -> None:
    async with serve(build_app(build_fake_registry())) as server, httpx.AsyncClient() as http:
        resp = await http.get(server.base_url + "/.well-known/oauth-protected-resource")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_configured"


async def test_protected_resource_metadata_matches_the_configured_public_url() -> None:
    from mcp.app import build_http_app, build_mcp_server  # noqa: PLC0415

    settings = Settings(
        jwt_secret_key="x" * 40,
        mcp_public_base_url="https://mcp.example.com",
        oauth_issuer="https://auth.example.com/",
    )
    metadata = resource_metadata_from_settings(settings, "/mcp")
    assert metadata is not None
    assert metadata.metadata_url == (
        "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
    )
    server_app = build_http_app(
        build_mcp_server(build_fake_registry(), resource_metadata=metadata),
        allowed_hosts=settings.mcp_allowed_hosts_list(),
    )
    async with serve(server_app) as server, httpx.AsyncClient() as http:
        for path in ("/.well-known/oauth-protected-resource", urlsplit(metadata.metadata_url).path):
            resp = await http.get(server.base_url + path)
            assert resp.status_code == 200, path
            assert resp.json() == {
                "resource": "https://mcp.example.com/mcp",
                "authorization_servers": ["https://auth.example.com"],
                "scopes_supported": ["mcp:read"],
                "bearer_methods_supported": ["header"],
                "resource_name": "Miru MCP",
                "resource_documentation": "https://modelcontextprotocol.io/specification/",
            }


async def test_without_an_authorization_server_no_metadata_is_configured() -> None:
    settings = Settings(jwt_secret_key="x" * 40, mcp_public_base_url="https://mcp.example.com")
    assert resource_metadata_from_settings(settings, "/mcp") is None


async def test_anonymous_requests_are_rate_limited_per_address() -> None:
    app = build_app(build_fake_registry(), rate_anonymous_per_minute=3)
    async with serve(app) as server, httpx.AsyncClient() as http:
        codes = [
            (await http.post(server.mcp_url, json=INITIALIZE, headers=MCP_HEADERS)).status_code
            for _ in range(4)
        ]
        assert codes == [200, 200, 200, 429]
        limited = await http.post(server.mcp_url, json=INITIALIZE, headers=MCP_HEADERS)
        assert int(limited.headers["retry-after"]) >= 1
        assert limited.json()["error"] == "rate_limited"
        # Health and discovery stay reachable for an operator while a client is throttled.
        assert (await http.get(server.base_url + "/healthz")).status_code == 200


async def test_forwarded_for_is_trusted_only_behind_a_configured_proxy() -> None:
    app = build_app(build_fake_registry(), rate_anonymous_per_minute=1, trusted_proxy_count=1)
    async with serve(app) as server, httpx.AsyncClient() as http:
        first = {**MCP_HEADERS, "x-forwarded-for": "203.0.113.1"}
        second = {**MCP_HEADERS, "x-forwarded-for": "203.0.113.2"}
        # Two real callers behind the tunnel are counted separately...
        assert (await http.post(server.mcp_url, json=INITIALIZE, headers=first)).status_code == 200
        assert (await http.post(server.mcp_url, json=INITIALIZE, headers=second)).status_code == 200
        # ...and a repeat from the first is the one that is throttled.
        assert (await http.post(server.mcp_url, json=INITIALIZE, headers=first)).status_code == 429


async def test_oversized_body_is_413() -> None:
    app = build_app(build_fake_registry(), max_body_bytes=2_000)
    async with serve(app) as server, httpx.AsyncClient() as http:
        big = {"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {"pad": "x" * 5_000}}
        resp = await http.post(server.mcp_url, json=big, headers=MCP_HEADERS)
        assert resp.status_code == 413
        assert resp.json()["error"] == "request_too_large"
        # A normal request still works afterwards.
        assert (
            await http.post(server.mcp_url, json=INITIALIZE, headers=MCP_HEADERS)
        ).status_code == 200


async def test_chunked_oversized_body_is_413() -> None:
    app = build_app(build_fake_registry(), max_body_bytes=2_000)

    async def chunks() -> object:
        for _ in range(10):
            yield b"x" * 1_000

    async with serve(app) as server, httpx.AsyncClient() as http:
        resp = await http.post(
            server.mcp_url,
            content=chunks(),
            headers=MCP_HEADERS,  # type: ignore[arg-type]
        )
        assert resp.status_code == 413


async def test_concurrency_cap_sheds_load_with_503_and_recovers() -> None:
    started, release = threading.Event(), threading.Event()
    registry = build_fake_registry()
    registry.register(SlowTool("slow_tool", started, release), immediate=True)
    app = build_app(registry, max_concurrent_requests=1)
    call = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "slow_tool", "arguments": {"value": 1}},
    }
    async with serve(app) as server, httpx.AsyncClient(timeout=20) as http:
        first = asyncio.create_task(http.post(server.mcp_url, json=call, headers=MCP_HEADERS))
        assert await asyncio.to_thread(started.wait, 10)
        shed = await http.post(server.mcp_url, json=INITIALIZE, headers=MCP_HEADERS)
        assert shed.status_code == 503
        assert shed.headers["retry-after"] == "1"
        release.set()
        assert (await first).status_code == 200
        ok = await http.post(server.mcp_url, json=INITIALIZE, headers=MCP_HEADERS)
        assert ok.status_code == 200


async def test_healthz_reports_facts_without_leaking_configuration() -> None:
    async with serve(build_app(build_fake_registry())) as server, httpx.AsyncClient() as http:
        resp = await http.get(server.base_url + "/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "miru"
        assert body["tools"] == {"total": 4, "listed": 3}
        assert isinstance(body["uptime_seconds"], int)
        assert body["version"]
        assert body["auth"] == {
            "required": False,
            "anonymous": True,
            "bearer": False,
            "oauth_metadata": False,
        }
        text = resp.text.lower()
        assert "secret" not in text
        assert "127.0.0.1" not in text
