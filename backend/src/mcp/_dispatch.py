"""Shared dispatch-to-`ToolResult` plumbing every MCP tool handler in this package uses.

Centralizes three things every handler needs identically: (1) always call `tools.base
.dispatch_tool` — never a tool's own `run()` — so the sponsor-entitlement gate is enforced
exactly like the internal agent loop; (2) render a successful result (real output or a masked
teaser — both are successful outcomes, never `isError`) as `structuredContent` *and* a JSON text
fallback, since not every MCP host reads `structuredContent` yet; (3) catch any exception a
tool's own `run()` raises and turn it into an `isError` result carrying a recovery hint, instead
of letting it propagate and take down the transport connection.
"""

from __future__ import annotations

from mcp._vendor import bootstrap_vendor_sdk

bootstrap_vendor_sdk()

import json  # noqa: E402

import structlog  # noqa: E402
from fastmcp.tools.base import ToolResult  # noqa: E402
from pydantic import BaseModel, ValidationError  # noqa: E402

from mcp.types import TextContent  # noqa: E402
from tools.base import MaskedTeaser, Tool, ToolContext, dispatch_tool  # noqa: E402

logger = structlog.get_logger(__name__)


def _text_block(payload: dict[str, object]) -> TextContent:
    return TextContent(type="text", text=json.dumps(payload))


def render_success(result: BaseModel | MaskedTeaser) -> ToolResult:
    """A real tool output or a `MaskedTeaser` are both successful results — never `isError`."""
    payload = result.model_dump(mode="json")
    return ToolResult(content=[_text_block(payload)], structured_content=payload)


def render_error(message: str) -> ToolResult:
    """An `isError` result with a plain-text recovery hint — never a raised exception."""
    return ToolResult(content=[TextContent(type="text", text=message)], is_error=True)


async def dispatch_and_render(
    tool: Tool,
    raw_args: dict[str, object],
    ctx: ToolContext,
) -> ToolResult:
    """Validate `raw_args` against `tool.input_model`, dispatch through the entitlement gate,
    and render the outcome — catching every failure mode (bad args, a tool-internal exception)
    as an `isError` result with a recovery hint instead of propagating."""
    try:
        parsed = tool.input_model.model_validate(raw_args)
    except ValidationError as exc:
        logger.info("mcp_tool_invalid_args", tool=tool.name, errors=exc.errors(include_url=False))
        return render_error(
            f"Invalid arguments for '{tool.name}': {exc.errors(include_url=False)!r}. "
            f"Check the tool's input schema and retry."
        )

    try:
        result = await dispatch_tool(tool, parsed, ctx)
    except Exception:  # any tool-internal failure must become isError, not crash
        logger.exception("mcp_tool_dispatch_failed", tool=tool.name)
        return render_error(
            f"'{tool.name}' failed while executing. This is not a bad-argument error — retry "
            f"later, or use 'search_tools' to find an alternative."
        )

    return render_success(result)


__all__ = ["dispatch_and_render", "render_error", "render_success"]
