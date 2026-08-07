"""The two MCP resources — `canonical://{symbol}` and `health://sources`.

Both are read-only projections of data this server already exposes as tools
(`get_canonical_context` / `get_source_health`) — registered as MCP *resources* as well because
a resource URI is how a host caches/references a symbol's canonical package without re-issuing
a tool call every turn. Neither resource reaches `context/`/`dataaccess/` itself: each renders
its already-registered tool's own output by dispatching through the same `tools.base
.dispatch_tool` gate every tool call goes through, so a resource read can never bypass the
sponsor-entitlement check or diverge from what the equivalent tool call would return.
"""

from __future__ import annotations

import json

import structlog
from pydantic import BaseModel

from schemas.enums import Market
from tools.base import MaskedTeaser, ToolContext, dispatch_tool
from tools.common import SymbolInput
from tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)

_CANONICAL_TOOL_NAME = "get_canonical_context"
_HEALTH_TOOL_NAME = "get_source_health"


def _parse_canonical_symbol(symbol: str) -> SymbolInput:
    """`canonical://{symbol}` URIs pass `symbol` as `"<id>.<MARKET>"` (e.g. `"2330.TW"`) —
    the same shape every symbol-scoped tool's `SymbolInput` expects, just flattened into one
    URI path segment. Falls back to `Market.TW` when no market suffix is present, since that's
    this server's overwhelmingly common case and an explicit suffix is always accepted."""
    if "." in symbol:
        raw_id, _, raw_market = symbol.rpartition(".")
        try:
            market = Market(raw_market.upper())
        except ValueError:
            return SymbolInput(id=symbol, market=Market.TW)
        return SymbolInput(id=raw_id, market=market)
    return SymbolInput(id=symbol, market=Market.TW)


def _render(result: BaseModel | MaskedTeaser) -> str:
    return json.dumps(result.model_dump(mode="json"))


async def read_canonical_resource(
    registry: ToolRegistry,
    symbol: str,
    *,
    ctx: ToolContext | None = None,
) -> str:
    """Render `canonical://{symbol}` — the same redacted payload `get_canonical_context`
    returns for that symbol, dispatched through the identical entitlement gate."""
    tool = registry.get_tool(_CANONICAL_TOOL_NAME)
    if tool is None:  # pragma: no cover — only if the registry loses this core tool
        logger.error("mcp_resource_missing_tool", tool=_CANONICAL_TOOL_NAME)
        return json.dumps({"error": f"{_CANONICAL_TOOL_NAME} is not registered"})
    args = _parse_canonical_symbol(symbol)
    result = await dispatch_tool(tool, args, ctx or ToolContext())
    return _render(result)


async def read_source_health_resource(
    registry: ToolRegistry,
    *,
    symbol: str = "2330.TW",
    ctx: ToolContext | None = None,
) -> str:
    """Render `health://sources` — the source-health diagnostics for one representative
    symbol's canonical build. `get_source_health` is itself a per-`(symbol, market)` tool (no
    market-wide health endpoint exists), so this resource takes an optional `symbol` query-style
    parameter with a sane default rather than pretending a symbol-free view exists."""
    tool = registry.get_tool(_HEALTH_TOOL_NAME)
    if tool is None:  # pragma: no cover — only if the registry loses this core tool
        logger.error("mcp_resource_missing_tool", tool=_HEALTH_TOOL_NAME)
        return json.dumps({"error": f"{_HEALTH_TOOL_NAME} is not registered"})
    args = _parse_canonical_symbol(symbol)
    result = await dispatch_tool(tool, args, ctx or ToolContext())
    return _render(result)


__all__ = ["read_canonical_resource", "read_source_health_resource"]
