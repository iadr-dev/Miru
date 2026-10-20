"""`private` tools (one user's own account data) are never offered over MCP: not listed, not found
by `search_tools`, not run by `execute_tool` - excluded by DATA CLASS, not by a hand-kept name
list, so a future private tool is covered with no edit here. The connector's consent screen
promises market data and analysis only."""

from __future__ import annotations

import asyncio

from mcp.app import MCP_EXCLUDED_TOOLS, build_mcp_server, is_mcp_offered
from schemas.enums import DataClass
from tests.mcp.conftest import FakeTool
from tools.registry import ToolRegistry


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(FakeTool("fake_public_core", "public core market read"), immediate=True)
    registry.register(
        FakeTool("fake_private_core", "my own watchlists", data_class=DataClass.PRIVATE),
        immediate=True,
    )
    registry.register(FakeTool("fake_public_deferred", "public watchlists alerts data"))
    registry.register(
        FakeTool(
            "fake_private_deferred",
            "my own watchlists alerts usage notifications",
            data_class=DataClass.PRIVATE,
        )
    )
    return registry


def test_the_private_names_are_not_on_the_hand_kept_list() -> None:
    """The exclusion is generic: it must hold with none of these names in MCP_EXCLUDED_TOOLS."""
    assert not {n for n in MCP_EXCLUDED_TOOLS if "private" in n or n.startswith("get_my_")}
    registry = _registry()
    offered = {t.name for t in registry.all_tools() if is_mcp_offered(t)}
    assert offered == {"fake_public_core", "fake_public_deferred"}


def test_a_private_tool_is_not_listed_searched_or_executed() -> None:
    server = build_mcp_server(_registry())

    async def run() -> None:
        listed = {t.name for t in await server.list_tools()}
        assert "fake_public_core" in listed
        assert "fake_private_core" not in listed

        found = await server.call_tool("search_tools", {"intent": "watchlists alerts usage"})
        names = {m["name"] for m in found.structured_content["matches"]}
        assert "fake_public_deferred" in names
        assert not {"fake_private_core", "fake_private_deferred"} & names

        for private in ("fake_private_core", "fake_private_deferred"):
            ran = await server.call_tool("execute_tool", {"id": private, "args": {"value": 1}})
            assert "Unknown tool id" in (ran.content[0].text if ran.content else "")
            assert not ran.structured_content or "doubled" not in ran.structured_content

        ok = await server.call_tool(
            "execute_tool", {"id": "fake_public_deferred", "args": {"value": 2}}
        )
        assert not ok.is_error
        assert ok.structured_content["doubled"] == 4

    asyncio.run(run())


def test_the_unknown_tool_hint_never_suggests_a_private_tool() -> None:
    server = build_mcp_server(_registry())

    async def run() -> None:
        ran = await server.call_tool("execute_tool", {"id": "fake_private_cor", "args": {}})
        text = ran.content[0].text if ran.content else ""
        assert "Unknown tool id" in text
        assert "fake_private" not in text.split("Did you mean:")[-1]

    asyncio.run(run())
