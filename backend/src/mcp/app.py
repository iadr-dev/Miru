"""The FastMCP app - a read-only projection of the tool registry over remote streamable HTTP.

Named `app.py` (not `server.py`) because the vendored SDK `fastmcp` depends on has its own
top-level `mcp.server` package - this project's own `mcp` package and that SDK's `mcp` package
share a name (see `mcp._vendor`), so a submodule here named `server` would be permanently
unreachable once the SDK claims `sys.modules["mcp.server"]` for itself.

Exposure model (mirrors the internal agent loop's own deferred-loading split): the registry's
`immediate_tools()` (the always-sent core) are each registered as a dedicated MCP tool with
full annotations; everything else (`deferred_index()`'s long tail) is reachable only via
`search_tools(intent)` / `execute_tool(id, args)`, so a client's context never has to hold every
tool's schema at once. Every dispatch - immediate or deferred - goes through `tools.base
.dispatch_tool`, never a tool's own `run()`, so the sponsor-entitlement gate (masked teaser for
an unentitled caller) is enforced identically to the internal loop; there is no separate
MCP-only code path for that check. Tools registered later (e.g. new macro tools) appear
automatically: this module reads only the registry.

This module builds the FastMCP app; it does not run it (no `app.run()`/uvicorn call here) - the
caller (`mcp.__main__`) decides transport wiring/lifecycle.
"""

from __future__ import annotations

from mcp._vendor import bootstrap_vendor_sdk

bootstrap_vendor_sdk()

import time  # noqa: E402
from collections.abc import Awaitable, Callable  # noqa: E402
from importlib import metadata  # noqa: E402
from typing import cast  # noqa: E402

import structlog  # noqa: E402
from fastmcp import FastMCP  # noqa: E402
from fastmcp.server.dependencies import get_http_headers  # noqa: E402
from fastmcp.server.http import StarletteWithLifespan  # noqa: E402
from fastmcp.tools.base import Tool as FastMCPTool  # noqa: E402
from fastmcp.tools.base import ToolResult  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, Response  # noqa: E402

from mcp._dispatch import dispatch_and_render, render_error, render_success  # noqa: E402
from mcp.discovery import WELL_KNOWN_PREFIX, ResourceMetadata  # noqa: E402
from mcp.principal import BearerResolver, PrincipalResolver, resolve_principal  # noqa: E402
from mcp.resources import (  # noqa: E402
    read_canonical_resource,
    read_source_health_resource,
)
from mcp.search import Candidate, rank  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402
from schemas.enums import DataClass  # noqa: E402
from tools.base import MaskedTeaser, Tool, ToolContext  # noqa: E402
from tools.registry import ToolRegistry  # noqa: E402

logger = structlog.get_logger(__name__)

SERVER_INSTRUCTIONS = """\
Miru is a read-only market-data and analysis service for Taiwan (TW/TWO) and US equities and ETFs, \
built for zh-TW and English users. Symbols are an id plus a market, e.g. {"id": "2330", \
"market": "TW"} for TSMC or {"id": "AAPL", "market": "US"}. Ask for data per symbol; the \
server does not scan the whole market.

How to use it:
- A handful of core tools are listed directly (canonical context, quote, company profile, source \
health). Everything else is reachable in two steps: call search_tools with a short description \
of what you need (English or Chinese), then call execute_tool with a returned tool name and its \
arguments (each search match includes its argument schema).
- Every result carries its own freshness caveat (how current the data is and where it came from). \
Quote that caveat when you report a number, and never present delayed or end-of-day data as live.
- Some data is sponsor-tier. If the caller's plan does not include it, the tool still succeeds \
but returns a masked teaser ({"locked": true, "required_tier": ..., "preview": ...}) instead of \
data. Tell the user that the data needs a Premium or Pro plan; do not guess the hidden values.
- Supply-chain relations, chain positions, the entity graph and concept-theme members are a \
preview unless the caller is on the Pro plan: a result with "locked": true (or "concept_locked": \
true) is limited and carries "hidden_count" and an "upgrade_note". Tell the user the full view is \
part of the Pro plan; do not retry to get more.
- A tool error (isError) includes a recovery hint: fix the arguments, or use search_tools to find \
another tool.

Hard rule - this is data and analysis only, NOT trading advice. Never recommend buying, selling \
or holding a security, never give price targets or position sizes, and do not present any output \
as a trading signal. Describe what the data shows and its limits.
"""

_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

_SEARCH_TOOLS_NAME = "search_tools"

# Tools an MCP caller should not get. Chat-only: each ends in a card the user confirms in the app
# (propose_*) or asks the person a question in the chat UI (ask_user); over MCP nobody would see
# the card, and the calling model could wrongly tell its user that an alert or schedule was set up.
# Costly or internal: web_search / fetch_url spend the operator's search budget and fetch arbitrary
# pages for any caller (MCP clients have their own browsing), and get_source_matrix lists the
# configured upstream sources, which is operator infrastructure, not market data. None of these is
# listed, found by search_tools or run by execute_tool. (Account tools are kept out by data class
# rather than by name - see `is_mcp_offered`.)
MCP_EXCLUDED_TOOLS: frozenset[str] = frozenset(
    {
        "ask_user",
        "fetch_url",
        "get_source_matrix",
        "propose_alert",
        "propose_memory",
        "propose_schedule",
        "propose_watchlist_change",
        "web_search",
    }
)


def is_mcp_offered(tool: Tool) -> bool:
    """Whether an MCP caller may list, search for or run `tool`. Excluded: the names above, and -
    by data class, so a future tool is covered without anyone remembering to list it - every
    `private` tool (one user's own watchlists, alerts, usage, notifications). The connector's
    consent screen promises market data and analysis only, so account data is not offered over MCP
    at all for now, signed in or not."""
    return tool.name not in MCP_EXCLUDED_TOOLS and tool.data_class is not DataClass.PRIVATE


_EXECUTE_TOOL_NAME = "execute_tool"
_MAX_SEARCH_RESULTS = 8
_MAX_SUGGESTIONS = 3
# Hint for clients that honor it (Claude Code's per-tool override of its output-token cap): these
# results are structured market data, not chatter.
_RESULT_SIZE_META: dict[str, object] = {"anthropic/maxResultSizeChars": 200_000}

type ContextProvider = Callable[[], Awaitable[ToolContext]]


def _title_for(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def _widen_for_teaser(schema: dict[str, object]) -> dict[str, object]:
    """A sponsor tool answers an unentitled caller with a `MaskedTeaser`, not its own output
    model, so its declared output schema has to accept either; otherwise a strict client would
    reject the (successful) teaser as not matching `outputSchema`."""
    teaser = MaskedTeaser.model_json_schema()
    defs: dict[str, object] = {}
    for part in (schema, teaser):
        part_defs = part.pop("$defs", None)
        if isinstance(part_defs, dict):
            defs.update(cast("dict[str, object]", part_defs))
    widened: dict[str, object] = {"type": "object", "anyOf": [schema, teaser]}
    if defs:
        widened["$defs"] = defs
    return widened


def _output_schema_for(tool: Tool) -> dict[str, object]:
    schema = tool.output_model.model_json_schema()
    if tool.data_class is DataClass.SPONSOR:
        return _widen_for_teaser(schema)
    return schema


class _RegistryBackedTool(FastMCPTool):
    """One immediate-core registry tool, projected as a dedicated MCP tool.

    Holds the underlying `tools.base.Tool` + the context provider this server resolves per call
    - `run()` always dispatches via `tools.base.dispatch_tool`, never `self.domain_tool.run()`
    directly, so entitlement masking stays identical to the internal agent loop.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    domain_tool: object  # tools.base.Tool - typed loosely here, Pydantic can't validate a Protocol
    context_provider: ContextProvider

    async def run(self, arguments: dict[str, object]) -> ToolResult:
        ctx = await self.context_provider()
        return await dispatch_and_render(self.domain_tool, arguments, ctx)  # type: ignore[arg-type]


def _immediate_tool(tool: Tool, *, context_provider: ContextProvider) -> FastMCPTool:
    return _RegistryBackedTool(
        name=tool.name,
        title=_title_for(tool.name),
        description=tool.description,
        parameters=tool.input_model.model_json_schema(),
        output_schema=_output_schema_for(tool),
        annotations=_READ_ONLY_ANNOTATIONS,
        meta=_RESULT_SIZE_META,
        domain_tool=tool,
        context_provider=context_provider,
    )


class SearchToolsInput(BaseModel):
    """The deferred-long-tail search input - a plain intent string, no symbol/date fields (this
    is a tool-catalog search, never a data read, so it carries none of the full-market-scan
    guard's per-symbol shape)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str = Field(
        min_length=1,
        description="What you're trying to accomplish or find, in English or Chinese.",
    )


class SearchToolsMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    data_class: str = Field(description="'public', or 'sponsor' (masked below Premium).")
    input_schema: dict[str, object] = Field(
        default_factory=dict, description="JSON Schema of the `args` to pass to execute_tool."
    )


class SearchToolsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    matches: tuple[SearchToolsMatch, ...] = Field(default_factory=tuple)


def _search_deferred_tools(registry: ToolRegistry, intent: str) -> SearchToolsOutput:
    candidates = [
        Candidate(e.name, e.description)
        for e in registry.deferred_index()
        if (tool := registry.get_tool(e.name)) is not None and is_mcp_offered(tool)
    ]
    matches: list[SearchToolsMatch] = []
    for hit in rank(intent, candidates, limit=_MAX_SEARCH_RESULTS):
        tool = registry.get_tool(hit.name)
        matches.append(
            SearchToolsMatch(
                name=hit.name,
                description=hit.description,
                data_class=tool.data_class.value if tool else DataClass.PUBLIC.value,
                input_schema=tool.input_model.model_json_schema() if tool else {},
            )
        )
    return SearchToolsOutput(matches=tuple(matches))


class ExecuteToolInput(BaseModel):
    """`id` is the tool's stable public name (from `search_tools`'s results); `args` is that
    tool's own input, passed through untyped here and validated against its real
    `input_model` at dispatch time - this input shape is deliberately generic since it fronts
    every deferred tool, each with a different schema."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, description="A tool name, e.g. from search_tools's results.")
    args: dict[str, object] = Field(
        default_factory=dict,
        description="Arguments for that tool, matching the input_schema search_tools returned.",
    )


# A tool's result (or a masked teaser) - the concrete shape differs per tool, so the contract
# here is "a JSON object".
_EXECUTE_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "description": "The target tool's structured result, or a masked teaser for locked data.",
    "additionalProperties": True,
}


class _SearchToolsTool(FastMCPTool):
    """`search_tools(intent)` - weighted scoring over `registry.deferred_index()` (see
    `mcp.search`). No embeddings."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    registry: ToolRegistry

    async def run(self, arguments: dict[str, object]) -> ToolResult:
        try:
            parsed = SearchToolsInput.model_validate(arguments)
        except Exception as exc:  # bad args must become isError, not a crashed connection
            return render_error(f"Invalid arguments for 'search_tools': {exc}")
        return render_success(_search_deferred_tools(self.registry, parsed.intent))


class _ExecuteToolTool(FastMCPTool):
    """`execute_tool(id, args)` - the deferred-long-tail dispatch primitive. `args` is
    validated against the target tool's own `input_model` inside `dispatch_and_render`, not
    against any schema fixed at registration time (every deferred tool has a different one)."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    registry: ToolRegistry
    context_provider: ContextProvider

    async def run(self, arguments: dict[str, object]) -> ToolResult:
        try:
            parsed = ExecuteToolInput.model_validate(arguments)
        except Exception as exc:  # bad args must become isError, not a crashed connection
            return render_error(f"Invalid arguments for 'execute_tool': {exc}")

        target = self.registry.get_tool(parsed.id)
        if target is None or not is_mcp_offered(target):
            return render_error(self._unknown_tool_message(parsed.id))
        ctx = await self.context_provider()
        return await dispatch_and_render(target, parsed.args, ctx)

    def _unknown_tool_message(self, tool_id: str) -> str:
        close = rank(
            tool_id,
            [
                Candidate(t.name, t.description)
                for t in self.registry.all_tools()
                if is_mcp_offered(t)
            ],
            limit=_MAX_SUGGESTIONS,
        )
        hint = f" Did you mean: {', '.join(c.name for c in close)}?" if close else ""
        return f"Unknown tool id '{tool_id}'.{hint} Use search_tools to find a valid tool id."


def _search_and_execute_tools(
    registry: ToolRegistry, *, context_provider: ContextProvider
) -> list[FastMCPTool]:
    search = _SearchToolsTool(
        name=_SEARCH_TOOLS_NAME,
        title="Search tools",
        description=(
            "Searches Miru's long-tail tool catalog by intent (English or Chinese) and "
            "returns the best matches with their descriptions and argument schemas; each "
            "match can be run with execute_tool."
        ),
        parameters=SearchToolsInput.model_json_schema(),
        output_schema=SearchToolsOutput.model_json_schema(),
        annotations=_READ_ONLY_ANNOTATIONS,
        meta=_RESULT_SIZE_META,
        registry=registry,
    )
    execute = _ExecuteToolTool(
        name=_EXECUTE_TOOL_NAME,
        title="Execute tool",
        description=(
            "Runs one registered tool by name with the given arguments and returns its "
            "structured result (or a masked teaser if the data needs a higher plan). "
            "Valid tool ids and their argument schemas come from search_tools."
        ),
        parameters=ExecuteToolInput.model_json_schema(),
        output_schema=_EXECUTE_OUTPUT_SCHEMA,
        annotations=_READ_ONLY_ANNOTATIONS,
        meta=_RESULT_SIZE_META,
        registry=registry,
        context_provider=context_provider,
    )
    return [search, execute]


def _package_version() -> str:
    try:
        return metadata.version("miru-backend")
    except metadata.PackageNotFoundError:  # pragma: no cover - only outside an installed env
        return "unknown"


def build_mcp_server(  # noqa: PLR0913 - one keyword per wiring seam; all optional
    registry: ToolRegistry,
    *,
    name: str = "miru",
    principal_resolver: PrincipalResolver | None = None,
    bearer_resolver: BearerResolver | None = None,
    resource_metadata: ResourceMetadata | None = None,
    bearer_enabled: bool = False,
    auth_required: bool = False,
) -> FastMCP:
    """Build (never runs) the FastMCP app for one already-assembled `ToolRegistry`.

    `principal_resolver` / `bearer_resolver` are the seams (see `mcp.principal`) that map an
    `x-api-key` / `Authorization: Bearer` credential to a user and tier - omit them to keep every
    caller anonymous/`Tier.FREE`.
    """
    mcp = FastMCP(name, instructions=SERVER_INSTRUCTIONS, version=_package_version())
    started = time.monotonic()

    async def context_provider() -> ToolContext:
        # include_all: FastMCP strips `authorization` from get_http_headers() by default.
        headers = get_http_headers(include_all=True)
        return await resolve_principal(
            headers, resolver=principal_resolver, bearer_resolver=bearer_resolver
        )

    # Registered in name order so tools/list is byte-for-byte stable across restarts and
    # registry reshuffles (clients and prompt caches key off it).
    listed = [
        _immediate_tool(t, context_provider=context_provider)
        for t in registry.immediate_tools()
        if is_mcp_offered(t)
    ]
    listed.extend(_search_and_execute_tools(registry, context_provider=context_provider))
    for fm_tool in sorted(listed, key=lambda t: t.name):
        mcp.add_tool(fm_tool)

    async def canonical_resource(symbol: str) -> str:
        return await read_canonical_resource(registry, symbol)

    mcp.resource(
        "canonical://{symbol}",
        name="canonical_context",
        title="Canonical context",
        description=(
            "The preserved canonical market-context package for one stock/ETF symbol "
            "(id.MARKET, e.g. '2330.TW') - the same redacted shape get_canonical_context returns."
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

    async def health_check(request: Request) -> Response:
        """Liveness plus a few non-sensitive facts for an operator or uptime probe; separate
        from `/mcp` (which needs a full MCP handshake to probe). Never includes settings,
        tokens or hostnames."""
        del request
        return JSONResponse(
            {
                "status": "ok",
                "service": name,
                "version": _package_version(),
                "uptime_seconds": int(time.monotonic() - started),
                "tools": {
                    "total": len([t for t in registry.all_tools() if is_mcp_offered(t)]),
                    "listed": sum(1 for t in registry.immediate_tools() if is_mcp_offered(t)) + 2,
                },
                "auth": {
                    "required": auth_required,
                    "anonymous": not auth_required,
                    "bearer": bearer_enabled,
                    "oauth_metadata": resource_metadata is not None,
                },
            }
        )

    mcp.custom_route("/healthz", methods=["GET"])(health_check)

    async def protected_resource_metadata(request: Request) -> Response:
        del request
        if resource_metadata is None:
            return JSONResponse(
                {
                    "error": "not_configured",
                    "message": "Sign-in is not configured (OAUTH_ISSUER, MCP_PUBLIC_BASE_URL).",
                },
                status_code=404,
            )
        return JSONResponse(resource_metadata.document(), headers={"cache-control": "max-age=300"})

    mcp.custom_route(WELL_KNOWN_PREFIX, methods=["GET"])(protected_resource_metadata)
    mcp.custom_route(f"{WELL_KNOWN_PREFIX}/mcp", methods=["GET"])(protected_resource_metadata)

    return mcp


def build_http_app(  # noqa: PLR0913 - mirrors FastMCP.http_app's own keyword surface
    mcp: FastMCP,
    *,
    path: str = "/mcp",
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    stateless_http: bool = False,
    gateway: list[Middleware] | None = None,
) -> StarletteWithLifespan:
    """Build the ASGI app for the streamable-HTTP transport, with DNS-rebinding protection on.

    `Host`/`Origin` header validation (`host_origin_protection="auto"`) is a spec MUST for the
    remote streamable-HTTP transport - enabled here rather than left to the caller's default.
    `MCP-Protocol-Version` header validation (HTTP 400 on an unsupported version) needs no
    wiring here at all: it is enforced unconditionally inside the underlying SDK's own
    streamable-HTTP request handler, on every request.
    """
    app = mcp.http_app(
        path=path,
        transport="streamable-http",
        stateless_http=stateless_http,
        host_origin_protection="auto",
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    # Outermost: FastMCP installs its Host/Origin guard as a middleware of its own, and a request
    # it rejects never reaches anything inside it. Ours must see (and explain) that rejection,
    # so `gateway` is placed in front of it rather than passed to `http_app(middleware=...)`.
    for layer in reversed(gateway or []):
        app.user_middleware.insert(0, layer)
    return app


__all__ = ["SERVER_INSTRUCTIONS", "build_http_app", "build_mcp_server", "is_mcp_offered"]
