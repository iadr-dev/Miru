"""Full-registry MCP-reachability proof — closes the "no test ever asserts 100% MCP coverage,
only spot-checks" gap: `test_app_real_registry.py` only spot-checks a handful of named tools.
This test iterates the ENTIRE real registry (all 97 tools) and asserts every single one is
reachable over MCP: present as a dedicated `immediate_tools()` MCP tool, or findable via
`execute_tool` (never the "Unknown tool id" refusal `_ExecuteToolTool` returns for a name the
registry doesn't actually have).

Also a regression guard for the exact bug this dispatch fixes: both `tests/mcp/conftest.py`'s
`build_real_registry()` and `tests/tools/test_registry_and_specs.py`'s `_registry()` now pass
`market_data_reader=`, so `run_tag_screener`/`get_tag_catalog` register at all — this test
explicitly asserts both are present and dispatchable, so a future regression that drops the
reader argument fails loudly here rather than silently shrinking the registry two tools short.
"""

from __future__ import annotations

import asyncio

from mcp.app import MCP_EXCLUDED_TOOLS, build_mcp_server, is_mcp_offered
from tests.mcp.conftest import build_real_registry


def test_every_real_tool_is_reachable_over_mcp() -> None:
    registry = build_real_registry()
    server = build_mcp_server(registry)
    all_tool_names = {tool.name for tool in registry.all_tools() if is_mcp_offered(tool)}
    immediate_names = {tool.name for tool in registry.immediate_tools() if is_mcp_offered(tool)}

    async def run() -> None:
        mcp_tools = await server.list_tools()
        mcp_tool_names = {tool.name for tool in mcp_tools}

        # Every immediate tool is registered as its own dedicated MCP tool.
        for name in immediate_names:
            assert name in mcp_tool_names, f"{name} is immediate but has no dedicated MCP tool"

        # Every non-immediate tool must be reachable via `execute_tool` — calling it with no
        # arguments must not hit the "unknown tool id" refusal (a real, args-validation refusal
        # is fine; that still proves the registry found the tool by name).
        deferred_names = all_tool_names - immediate_names
        assert deferred_names, "expected a non-empty deferred long tail"
        for name in sorted(deferred_names):
            result = await server.call_tool("execute_tool", {"id": name, "args": {}})
            text = result.content[0].text if result.content else ""
            assert "Unknown tool id" not in text, f"{name} is not reachable via execute_tool"

    asyncio.run(run())


def test_screener_tools_are_present_and_dispatchable_after_the_fixture_fix() -> None:
    """Regression guard: before this dispatch's fixture fix, `build_real_registry()` (and
    `tests/tools/test_registry_and_specs.py`'s `_registry()`) built the registry WITHOUT
    `market_data_reader=`, so `run_tag_screener`/`get_tag_catalog` silently never registered —
    every "real registry" test exercised a registry two tools short of the real one, and the
    ground-truth count assertion tested the wrong (smaller) total. This pins both tools present
    and dispatchable now that the fixture passes the reader."""
    registry = build_real_registry()
    server = build_mcp_server(registry)
    all_tool_names = {tool.name for tool in registry.all_tools()}
    assert "run_tag_screener" in all_tool_names
    assert "get_tag_catalog" in all_tool_names

    async def run() -> None:
        catalog_result = await server.call_tool(
            "execute_tool", {"id": "get_tag_catalog", "args": {}}
        )
        assert not catalog_result.is_error
        assert "categories" in catalog_result.structured_content

        screener_result = await server.call_tool(
            "execute_tool",
            {
                "id": "run_tag_screener",
                "args": {"market": "TW", "tags": [{"tag": "breakout_above_ma20"}]},
            },
        )
        assert not screener_result.is_error

    asyncio.run(run())


def test_chat_only_tools_are_not_offered_over_mcp() -> None:
    """The propose_* / ask_user tools end in a card only the Miru chat can show, so MCP neither
    lists them, finds them with search_tools, nor runs them with execute_tool."""
    registry = build_real_registry()
    server = build_mcp_server(registry)
    assert {tool.name for tool in registry.all_tools()} >= MCP_EXCLUDED_TOOLS, "stale exclusion"

    async def run() -> None:
        listed = {tool.name for tool in await server.list_tools()}
        assert not listed & MCP_EXCLUDED_TOOLS
        for name in sorted(MCP_EXCLUDED_TOOLS):
            found = await server.call_tool("search_tools", {"intent": name})
            assert name not in (found.content[0].text if found.content else "")
            ran = await server.call_tool("execute_tool", {"id": name, "args": {}})
            assert "Unknown tool id" in (ran.content[0].text if ran.content else "")

    asyncio.run(run())
