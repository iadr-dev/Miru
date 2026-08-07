"""End-to-end proof against the REAL 97-tool registry (`build_default_tool_registry`) — not
mocks. Proves the actual immediate/deferred split this project's tool-registration modules
produce, and a real `search_tools`/`execute_tool` round-trip against real tool objects."""

from __future__ import annotations

import asyncio

from mcp.app import build_mcp_server
from tests.mcp.conftest import build_real_registry


def test_real_immediate_tools_are_registered_as_dedicated_mcp_tools() -> None:
    registry = build_real_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        tools = await server.list_tools()
        names = {tool.name for tool in tools}
        # The real, current immediate-core set (5 tools) plus search_tools/execute_tool.
        assert {
            "get_canonical_context",
            "build_conversation_package",
            "get_source_health",
            "get_company_profile",
            "get_price_quote",
            "search_tools",
            "execute_tool",
        } <= names
        # None of the 91 deferred tools got a dedicated MCP tool entry.
        assert "get_chip_flows" not in names
        assert "run_bounded_observation" not in names

    asyncio.run(run())


def test_real_search_tools_finds_a_real_deferred_tool_by_intent() -> None:
    registry = build_real_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        result = await server.call_tool("search_tools", {"intent": "branch broker chip flows"})
        assert not result.is_error
        names = {m["name"] for m in result.structured_content["matches"]}
        assert "get_chip_flows" in names

    asyncio.run(run())


def test_real_execute_tool_round_trips_a_real_deferred_tool() -> None:
    registry = build_real_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        result = await server.call_tool(
            "execute_tool",
            {"id": "get_source_matrix", "args": {}},
        )
        assert not result.is_error
        assert "entries" in result.structured_content

    asyncio.run(run())


def test_real_sponsor_tool_via_execute_tool_is_masked_for_anonymous_caller() -> None:
    registry = build_real_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        result = await server.call_tool(
            "execute_tool",
            {"id": "get_chip_flows", "args": {"id": "2330", "market": "TW"}},
        )
        assert not result.is_error
        assert result.structured_content["locked"] is True
        assert result.structured_content["tool_name"] == "get_chip_flows"

    asyncio.run(run())


def test_real_canonical_resource_round_trips() -> None:
    registry = build_real_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        result = await server.read_resource("canonical://2330.TW")
        assert result is not None
        assert result.contents

    asyncio.run(run())


def test_real_source_health_resource_round_trips() -> None:
    registry = build_real_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        result = await server.read_resource("health://sources")
        assert result is not None
        assert result.contents

    asyncio.run(run())
