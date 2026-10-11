"""The tool registry — immediate-core vs deferred-long-tail, with lazy loading of the deferred
set.

With ~50 registry tools (only a handful built this phase — see each tool group module for
which), sending every schema every turn wastes context. Following Pi's `splitDeferredTools()`,
tools split into:

- **immediate** — the canonical core whose full schema is always sent. The design names this
  set once (`get_canonical_context`, `get_market_context`, `get_company_profile`,
  `build_conversation_package`, `get_source_health`, + the basic news/price-quote read) — the
  single source of truth; this registry does not re-derive or override that list, it is
  wired in `tools/context_tools.py`/`tools/market/` at registration time (see each
  module's docstring for exactly which of the named core tools this phase built vs.
  deferred a build of because the underlying capability doesn't exist yet).
- **deferred** — the long tail, exposed to the model only as a compact index (name + one-line
  description); `get_tool(name)` loads/dispatches the full tool on demand — the mechanism a
  future `execute_tool(id, args)` endpoint will call into.

This module is pure bookkeeping: it holds no capability/context logic itself, only tool
registration + the immediate/deferred split + name-based lookup.
"""

from __future__ import annotations

from dataclasses import dataclass

from tools.base import Tool


@dataclass(frozen=True)
class DeferredIndexEntry:
    """One compact index entry for a deferred (not-immediately-sent) tool."""

    name: str
    description: str


class ToolRegistry:
    """Registration + the immediate/deferred split + `get_tool` dispatch lookup.

    A tool is registered exactly once, tagged `immediate=True` (always-sent core) or
    `immediate=False` (deferred long-tail, default — per the "Add a tool" recipe: default to
    deferred unless a tool is part of the named immediate core). `get_tool(name)` works
    identically for both — deferred-ness only affects what `immediate_tools()`/
    `deferred_index()` expose to the model up front, never whether a tool is *reachable*.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._immediate: set[str] = set()

    def register(self, tool: Tool, *, immediate: bool = False) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        if immediate:
            self._immediate.add(tool.name)

    def get_tool(self, name: str) -> Tool | None:
        """Look up a tool by name — the primitive a future `execute_tool(id, args)` endpoint
        dispatches through. Returns `None` (never raises) for an
        unknown name; the caller decides how to surface "unknown tool" to the model/MCP host."""
        return self._tools.get(name)

    def immediate_tools(self) -> list[Tool]:
        """The always-sent core set, full schemas — order follows registration order."""
        return [tool for name, tool in self._tools.items() if name in self._immediate]

    def deferred_index(self) -> list[DeferredIndexEntry]:
        """The long-tail index (name + one-line description only) for tools not in the
        immediate core — what a future `search_tools(intent)` endpoint filters over."""
        return [
            DeferredIndexEntry(name=tool.name, description=tool.description)
            for name, tool in self._tools.items()
            if name not in self._immediate
        ]

    def all_tools(self) -> list[Tool]:
        """Every registered tool, immediate and deferred alike — for tests/introspection."""
        return list(self._tools.values())


def build_default_registry(*tool_groups: list[tuple[Tool, bool]]) -> ToolRegistry:
    """Convenience assembler: each tool group module exposes a
    `list[tuple[Tool, immediate: bool]]` (its own registration table); this composes them into
    one `ToolRegistry`. Groups are registered in the order given."""
    registry = ToolRegistry()
    for group in tool_groups:
        for tool, immediate in group:
            registry.register(tool, immediate=immediate)
    return registry


__all__ = ["DeferredIndexEntry", "ToolRegistry", "build_default_registry"]
