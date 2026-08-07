"""The FastMCP app — a read-only projection of the tool registry over remote streamable HTTP.

Named `app.py` (not `server.py`) because the vendored SDK `fastmcp` depends on has its own
top-level `mcp.server` package — this project's own `mcp` package and that SDK's `mcp` package
share a name (see `mcp._vendor`), so a submodule here named `server` would be permanently
unreachable once the SDK claims `sys.modules["mcp.server"]` for itself.

Exposure model (mirrors the internal agent loop's own deferred-loading split): the registry's
`immediate_tools()` (the always-sent core) are each registered as a dedicated MCP tool with
full annotations; everything else (`deferred_index()`'s long tail) is reachable only via
`search_tools(intent)` / `execute_tool(id, args)`, so a client's context never has to hold every
tool's schema at once. Every dispatch — immediate or deferred — goes through `tools.base
.dispatch_tool`, never a tool's own `run()`, so the sponsor-entitlement gate (masked teaser for
an unentitled caller) is enforced identically to the internal loop; there is no separate
MCP-only code path for that check.

This module builds the FastMCP app; it does not run it (no `app.run()`/uvicorn call here) — the
caller (a future process entry point) decides transport wiring/lifecycle.
"""

from __future__ import annotations

from mcp._vendor import bootstrap_vendor_sdk

bootstrap_vendor_sdk()

import structlog  # noqa: E402
from fastmcp import FastMCP  # noqa: E402
from fastmcp.server.dependencies import get_http_headers  # noqa: E402
from fastmcp.server.http import StarletteWithLifespan  # noqa: E402
from fastmcp.tools.base import Tool as FastMCPTool  # noqa: E402
from fastmcp.tools.base import ToolResult  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402
from starlette.responses import JSONResponse, Response  # noqa: E402

from mcp._dispatch import dispatch_and_render, render_error, render_success  # noqa: E402
from mcp.principal import PrincipalResolver, resolve_principal  # noqa: E402
from mcp.resources import (  # noqa: E402
    read_canonical_resource,
    read_source_health_resource,
)
from mcp.types import ToolAnnotations  # noqa: E402
from tools.registry import ToolRegistry  # noqa: E402

logger = structlog.get_logger(__name__)

_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

_SEARCH_TOOLS_NAME = "search_tools"
_EXECUTE_TOOL_NAME = "execute_tool"
_MAX_SEARCH_RESULTS = 10


class _RegistryBackedTool(FastMCPTool):
    """One immediate-core registry tool, projected as a dedicated MCP tool.

    Holds the underlying `tools.base.Tool` + the `ToolContext` this server resolves per call —
    `run()` always dispatches via `tools.base.dispatch_tool`, never `self.domain_tool.run()`
    directly, so entitlement masking stays identical to the internal agent loop.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    domain_tool: object  # tools.base.Tool — typed loosely here, Pydantic can't validate a Protocol
    principal_resolver: PrincipalResolver | None = None

    async def run(self, arguments: dict[str, object]) -> ToolResult:
        headers = get_http_headers()
        ctx = await resolve_principal(headers, resolver=self.principal_resolver)
        return await dispatch_and_render(self.domain_tool, arguments, ctx)  # type: ignore[arg-type]


def _register_immediate_tool(
    mcp: FastMCP,
    tool: object,  # tools.base.Tool
    *,
    principal_resolver: PrincipalResolver | None,
) -> None:
    fm_tool = _RegistryBackedTool(
        name=tool.name,  # type: ignore[attr-defined]
        description=tool.description,  # type: ignore[attr-defined]
        parameters=tool.input_model.model_json_schema(),  # type: ignore[attr-defined]
        output_schema=tool.output_model.model_json_schema(),  # type: ignore[attr-defined]
        annotations=_READ_ONLY_ANNOTATIONS,
        domain_tool=tool,
        principal_resolver=principal_resolver,
    )
    mcp.add_tool(fm_tool)


class SearchToolsInput(BaseModel):
    """The deferred-long-tail search input — a plain intent string, no symbol/date fields (this
    is a tool-catalog search, never a data read, so it carries none of the full-market-scan
    guard's per-symbol shape)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str = Field(min_length=1, description="What you're trying to accomplish or find.")


class SearchToolsMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str


class SearchToolsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    matches: tuple[SearchToolsMatch, ...] = Field(default_factory=tuple)


def _keyword_score(intent_terms: set[str], name: str, description: str) -> int:
    haystack = f"{name} {description}".lower()
    return sum(1 for term in intent_terms if term in haystack)


def _search_deferred_tools(registry: ToolRegistry, intent: str) -> SearchToolsOutput:
    intent_terms = {term for term in intent.lower().replace("_", " ").split() if term}
    scored = [
        (entry, _keyword_score(intent_terms, entry.name, entry.description))
        for entry in registry.deferred_index()
    ]
    ranked = sorted((s for s in scored if s[1] > 0), key=lambda s: s[1], reverse=True)
    matches = tuple(
        SearchToolsMatch(name=entry.name, description=entry.description)
        for entry, _score in ranked[:_MAX_SEARCH_RESULTS]
    )
    return SearchToolsOutput(matches=matches)


class ExecuteToolInput(BaseModel):
    """`id` is the tool's stable public name (from `search_tools`'s results); `args` is that
    tool's own input, passed through untyped here and validated against its real
    `input_model` at dispatch time — this input shape is deliberately generic since it fronts
    every deferred tool, each with a different schema."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, description="A tool name, e.g. from search_tools's results.")
    args: dict[str, object] = Field(default_factory=dict)


class _SearchToolsTool(FastMCPTool):
    """`search_tools(intent)` — a keyword/substring match over `registry.deferred_index()`'s
    name+description pairs. First-pass matching only (no embeddings/semantic search)."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    registry: ToolRegistry

    async def run(self, arguments: dict[str, object]) -> ToolResult:
        try:
            parsed = SearchToolsInput.model_validate(arguments)
        except Exception as exc:  # bad args must become isError, not a crashed connection
            return render_error(f"Invalid arguments for 'search_tools': {exc}")
        output = _search_deferred_tools(self.registry, parsed.intent)
        return render_success(output)


class _ExecuteToolTool(FastMCPTool):
    """`execute_tool(id, args)` — the deferred-long-tail dispatch primitive. `args` is
    validated against the target tool's own `input_model` inside `dispatch_and_render`, not
    against any schema fixed at registration time (every deferred tool has a different one)."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    registry: ToolRegistry
    principal_resolver: PrincipalResolver | None = None

    async def run(self, arguments: dict[str, object]) -> ToolResult:
        try:
            parsed = ExecuteToolInput.model_validate(arguments)
        except Exception as exc:  # bad args must become isError, not a crashed connection
            return render_error(f"Invalid arguments for 'execute_tool': {exc}")

        target = self.registry.get_tool(parsed.id)
        if target is None:
            return render_error(
                f"Unknown tool id '{parsed.id}'. Use search_tools to find a valid tool id."
            )
        headers = get_http_headers()
        ctx = await resolve_principal(headers, resolver=self.principal_resolver)
        return await dispatch_and_render(target, parsed.args, ctx)


def _register_search_and_execute(
    mcp: FastMCP,
    registry: ToolRegistry,
    *,
    principal_resolver: PrincipalResolver | None,
) -> None:
    mcp.add_tool(
        _SearchToolsTool(
            name=_SEARCH_TOOLS_NAME,
            title="Search tools",
            description=(
                "Search the deferred long-tail tool index by intent and return matching tool "
                "names and descriptions. Use execute_tool to run a match."
            ),
            parameters=SearchToolsInput.model_json_schema(),
            output_schema=SearchToolsOutput.model_json_schema(),
            annotations=_READ_ONLY_ANNOTATIONS,
            registry=registry,
        )
    )
    mcp.add_tool(
        _ExecuteToolTool(
            name=_EXECUTE_TOOL_NAME,
            title="Execute tool",
            description=(
                "Run one registered tool by name with the given arguments and return its "
                "structured result. Use search_tools first to find a valid tool id."
            ),
            parameters=ExecuteToolInput.model_json_schema(),
            output_schema=None,
            annotations=_READ_ONLY_ANNOTATIONS,
            registry=registry,
            principal_resolver=principal_resolver,
        )
    )


def build_mcp_server(
    registry: ToolRegistry,
    *,
    name: str = "miro",
    principal_resolver: PrincipalResolver | None = None,
) -> FastMCP:
    """Build (never runs) the FastMCP app for one already-assembled `ToolRegistry`.

    `principal_resolver` is the Phase-9 seam (see `mcp.principal`) — omit it to keep every
    caller anonymous/`Tier.FREE` (the safe default with no accounts system wired up yet).
    """
    mcp = FastMCP(name)

    for tool in registry.immediate_tools():
        _register_immediate_tool(mcp, tool, principal_resolver=principal_resolver)

    _register_search_and_execute(mcp, registry, principal_resolver=principal_resolver)

    async def canonical_resource(symbol: str) -> str:
        return await read_canonical_resource(registry, symbol)

    mcp.resource(
        "canonical://{symbol}",
        name="canonical_context",
        title="Canonical context",
        description=(
            "The preserved canonical market-context package for one stock/ETF symbol "
            "(id.MARKET, e.g. '2330.TW') — the same redacted shape get_canonical_context returns."
        ),
        mime_type="application/json",
    )(canonical_resource)

    async def source_health_resource() -> str:
        return await read_source_health_resource(registry)

    mcp.resource(
        "health://sources",
        name="source_health",
        title="Source health",
        description="Source-health diagnostics behind a symbol's canonical context build.",
        mime_type="application/json",
    )(source_health_resource)

    async def health_check(request: object) -> Response:
        """A liveness endpoint separate from `/mcp` — hosts/orchestrators can poll this
        without standing up a full MCP session, which the protocol endpoint requires."""
        del request
        return JSONResponse({"status": "ok"})

    mcp.custom_route("/healthz", methods=["GET"])(health_check)

    return mcp


def build_http_app(
    mcp: FastMCP,
    *,
    path: str = "/mcp",
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
) -> StarletteWithLifespan:
    """Build the ASGI app for the streamable-HTTP transport, with DNS-rebinding protection on.

    `Host`/`Origin` header validation (`host_origin_protection="auto"`) is a spec MUST for the
    remote streamable-HTTP transport — enabled here rather than left to the caller's default.
    `MCP-Protocol-Version` header validation (HTTP 400 on an unsupported version) needs no
    wiring here at all: it is enforced unconditionally inside the underlying SDK's own
    streamable-HTTP request handler, on every request, regardless of any option this module
    passes.
    """
    return mcp.http_app(
        path=path,
        transport="streamable-http",
        host_origin_protection="auto",
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


__all__ = ["build_http_app", "build_mcp_server"]
