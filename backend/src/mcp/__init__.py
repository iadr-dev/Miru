"""MCP server package (`backend/src/mcp/`) — a read-only projection of the tool registry.

Bootstraps the real `fastmcp`/`mcp` SDK on package import (see `mcp._vendor` for why this is
necessary — this package's own top-level name collides with a different package the SDK
depends on) so every submodule here can `import fastmcp` / `from mcp.types import ...` safely,
without each one re-running the fixup itself.
"""

from __future__ import annotations

from mcp._vendor import bootstrap_vendor_sdk

bootstrap_vendor_sdk()
