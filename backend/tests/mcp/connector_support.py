"""Builders shared by the connector-compatibility tests: the real FastMCP app wrapped in the real
gateway middleware, served by a real uvicorn (see `live_server`), driven by real clients."""

from __future__ import annotations

from mcp._vendor import bootstrap_vendor_sdk

bootstrap_vendor_sdk()

import asyncio  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from dataclasses import replace  # noqa: E402

import httpx  # noqa: E402
from fastmcp import Client  # noqa: E402
from fastmcp.client.transports import StreamableHttpTransport  # noqa: E402
from fastmcp.server.http import StarletteWithLifespan  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402

from events.rate_limit import AsyncRateLimiter, InMemoryAsyncRateLimiter  # noqa: E402
from mcp.app import build_http_app, build_mcp_server  # noqa: E402
from mcp.gateway import GatewayConfig, McpGateway, TokenVerifier  # noqa: E402
from mcp.principal import BearerResolver, PrincipalResolver  # noqa: E402
from tests.mcp.conftest import FakeTool, FakeToolInput, FakeToolOutput  # noqa: E402
from tests.mcp.live_server import LiveServer  # noqa: E402
from tools.base import ToolContext  # noqa: E402
from tools.registry import ToolRegistry  # noqa: E402

DEFAULT_GATEWAY = GatewayConfig(
    mcp_path="/mcp",
    max_body_bytes=1_048_576,
    max_concurrent_requests=64,
    rate_anonymous_per_minute=10_000,
    rate_signed_in_per_minute=10_000,
)

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1"},
    },
}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


class SlowTool(FakeTool):
    """A tool that parks until released, to hold a request in flight."""

    def __init__(self, name: str, started: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__(name, "Parks until released.")
        self.started = started
        self.release = release

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del args, ctx
        self.started.set()
        await self.release.wait()
        return FakeToolOutput(doubled=0)


def build_app(  # noqa: PLR0913 - test builder: one knob per seam under test
    registry: ToolRegistry,
    *,
    gateway: GatewayConfig = DEFAULT_GATEWAY,
    rate_limiter: AsyncRateLimiter | None = None,
    verify_token: TokenVerifier | None = None,
    principal_resolver: PrincipalResolver | None = None,
    bearer_resolver: BearerResolver | None = None,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    stateless_http: bool = True,
    **gateway_overrides: int | str | None,
) -> StarletteWithLifespan:
    config = replace(gateway, **gateway_overrides) if gateway_overrides else gateway
    server = build_mcp_server(
        registry,
        principal_resolver=principal_resolver,
        bearer_resolver=bearer_resolver,
        bearer_enabled=verify_token is not None,
    )
    middleware = Middleware(
        McpGateway,
        config=config,
        rate_limiter=rate_limiter or InMemoryAsyncRateLimiter(),
        verify_token=verify_token or (lambda _token: None),
    )
    return build_http_app(
        server,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        stateless_http=stateless_http,
        gateway=[middleware],
    )


@asynccontextmanager
async def official_session(
    server: LiveServer, *, headers: dict[str, str] | None = None, url: str | None = None
) -> AsyncIterator[ClientSession]:
    """The official `mcp` SDK client over streamable HTTP, initialized."""
    async with (
        httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=False) as http_client,
        streamable_http_client(url or server.mcp_url, http_client=http_client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def fastmcp_client(
    server: LiveServer, *, headers: dict[str, str] | None = None, url: str | None = None
) -> Client[StreamableHttpTransport]:
    return Client(StreamableHttpTransport(url or server.mcp_url, headers=headers))


__all__ = [
    "DEFAULT_GATEWAY",
    "INITIALIZE",
    "MCP_HEADERS",
    "FakeToolInput",
    "SlowTool",
    "build_app",
    "fastmcp_client",
    "official_session",
]
