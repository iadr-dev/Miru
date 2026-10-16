"""The production MCP-process composition root — assembles the real `FastMCP` server + its ASGI
app from config/env, mirroring how `scheduler/composition.py` and `api/composition.py` each
assemble their own process's real collaborator graph from the same shared interaction-layer
builders (`orchestrator.composition`).

No network I/O at construction time: `build_tool_registry` resolves the real data-access router
and session factory from config, but neither performs a live fetch until a tool actually runs.

Principal resolution: `mcp.accounts_resolver.AccountsPrincipalResolver` maps the application's own
signed access JWT (`Authorization: Bearer` or `x-api-key`) to the real user's tier; no credential
stays anonymous/`Tier.FREE`. `mcp.gateway.McpGateway` is the HTTP edge (401 challenge, rate limit,
body and concurrency caps, trailing-slash normalization).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastmcp.server.http import StarletteWithLifespan
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware import Middleware

from config.settings import Settings, get_settings
from dataaccess.composition import build_default_router
from events.rate_limit import AsyncRateLimiter, build_rate_limiter
from mcp.accounts_resolver import AccountsPrincipalResolver, verify_access_token
from mcp.app import build_http_app, build_mcp_server
from mcp.discovery import resource_metadata_from_settings
from mcp.gateway import GatewayConfig, McpGateway
from orchestrator.composition import build_session_factory, build_tool_registry

MCP_PATH = "/mcp"


@dataclass(frozen=True)
class McpApp:
    """The built ASGI app plus its session factory — the caller (`mcp/__main__.py`) needs the
    latter to `await ensure_schema_for(...)` once before serving, separately from construction
    (this composition root performs no network I/O at construction time; a DB connection is
    network I/O, so schema provisioning is deliberately the caller's job, not this one's)."""

    app: StarletteWithLifespan
    session_factory: async_sessionmaker[AsyncSession]


def build_mcp_http_app(
    settings: Settings | None = None,
    *,
    rate_limiter: AsyncRateLimiter | None = None,
) -> McpApp:
    """Assemble the real, fully-wired MCP ASGI app from config - the composition root the
    standalone MCP process (`python -m mcp`) invokes."""
    resolved = settings or get_settings()
    session_factory = build_session_factory(resolved)
    router = build_default_router(resolved)
    tool_registry = build_tool_registry(router, session_factory=session_factory)

    accounts = AccountsPrincipalResolver(settings=resolved, session_factory=session_factory)
    bearer_enabled = resolved.jwt_secret_key is not None
    resource_metadata = resource_metadata_from_settings(resolved, MCP_PATH)
    server = build_mcp_server(
        tool_registry,
        principal_resolver=accounts,
        bearer_resolver=accounts,
        resource_metadata=resource_metadata,
        bearer_enabled=bearer_enabled,
    )
    gateway = Middleware(
        McpGateway,
        config=GatewayConfig(
            mcp_path=MCP_PATH,
            max_body_bytes=resolved.mcp_max_body_bytes,
            max_concurrent_requests=resolved.mcp_max_concurrent_requests,
            rate_anonymous_per_minute=resolved.mcp_rate_limit_anonymous_per_minute,
            rate_signed_in_per_minute=resolved.mcp_rate_limit_signed_in_per_minute,
            trusted_proxy_count=resolved.trusted_proxy_count,
            resource_metadata_url=resource_metadata.metadata_url if resource_metadata else None,
        ),
        rate_limiter=rate_limiter or build_rate_limiter(resolved),
        verify_token=lambda token: verify_access_token(token, resolved),
    )
    app = build_http_app(
        server,
        path=MCP_PATH,
        allowed_hosts=resolved.mcp_allowed_hosts_list(),
        allowed_origins=resolved.mcp_allowed_origins_list(),
        stateless_http=resolved.mcp_stateless_http,
        gateway=[gateway],
    )
    return McpApp(app=app, session_factory=session_factory)


__all__ = ["McpApp", "build_mcp_http_app"]
