"""The production MCP-process composition root — assembles the real `FastMCP` server + its ASGI
app from config/env, mirroring how `scheduler/composition.py` and `api/composition.py` each
assemble their own process's real collaborator graph from the same shared interaction-layer
builders (`orchestrator.composition`).

No network I/O at construction time: `build_tool_registry` resolves the real data-access router
and session factory from config, but neither performs a live fetch until a tool actually runs.

Principal resolution stays at the safe default (`mcp.principal.AnonymousOnlyResolver`, via
`build_mcp_server`'s own `principal_resolver=None` default) — no accounts-backed, API-key-to-user
lookup exists yet (that is `accounts/`'s job, once a phase wires an MCP-facing API-key store), so
this composition root does not fabricate one. Wiring a real resolver here later is a one-line
change at this one call site, exactly the seam `mcp.principal.PrincipalResolver` exists for.
"""

from __future__ import annotations

from fastmcp.server.http import StarletteWithLifespan

from config.settings import Settings, get_settings
from dataaccess.composition import build_default_router
from mcp.app import build_http_app, build_mcp_server
from orchestrator.composition import build_session_factory, build_tool_registry


def build_mcp_http_app(settings: Settings | None = None) -> StarletteWithLifespan:
    """Assemble the real, fully-wired MCP ASGI app from config — the composition root the
    standalone MCP process (`python -m mcp`) invokes."""
    resolved = settings or get_settings()
    session_factory = build_session_factory(resolved)
    router = build_default_router(resolved)
    tool_registry = build_tool_registry(router, session_factory=session_factory)
    server = build_mcp_server(tool_registry)
    return build_http_app(
        server,
        allowed_hosts=resolved.mcp_allowed_hosts_list(),
        allowed_origins=resolved.mcp_allowed_origins_list(),
    )


__all__ = ["build_mcp_http_app"]
