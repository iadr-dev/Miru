"""`mcp.app.build_mcp_server` against a fake registry — the annotation/exposure/error-
handling contract, isolated from any real tool/network concern."""

from __future__ import annotations

import asyncio

from mcp.types import ToolAnnotations

from mcp.app import build_http_app, build_mcp_server
from tests.mcp.conftest import build_fake_registry


def test_immediate_tools_are_registered_with_full_readonly_annotations() -> None:
    registry = build_fake_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        tool = await server.get_tool("fake_immediate")
        assert tool.annotations == ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        )

    asyncio.run(run())


def test_deferred_tools_are_not_registered_as_dedicated_mcp_tools() -> None:
    registry = build_fake_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        tools = await server.list_tools()
        names = {tool.name for tool in tools}
        assert "fake_deferred_chip_flows" not in names
        assert "fake_sponsor_tool" not in names
        assert "search_tools" in names
        assert "execute_tool" in names

    asyncio.run(run())


def test_search_tools_finds_a_deferred_tool_by_intent() -> None:
    registry = build_fake_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        result = await server.call_tool("search_tools", {"intent": "chip broker flows"})
        assert not result.is_error
        names = {m["name"] for m in result.structured_content["matches"]}
        assert "fake_deferred_chip_flows" in names

    asyncio.run(run())


def test_search_tools_with_no_match_returns_empty_not_an_error() -> None:
    registry = build_fake_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        result = await server.call_tool(
            "search_tools", {"intent": "something completely unrelated zzz"}
        )
        assert not result.is_error
        assert result.structured_content["matches"] == []

    asyncio.run(run())


def test_execute_tool_dispatches_a_real_deferred_tool_and_returns_structured_output() -> None:
    registry = build_fake_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        result = await server.call_tool(
            "execute_tool",
            {"id": "fake_deferred_chip_flows", "args": {"value": 4}},
        )
        assert not result.is_error
        assert result.structured_content == {"doubled": 8}

    asyncio.run(run())


def test_execute_tool_on_sponsor_tool_returns_masked_teaser_not_an_error() -> None:
    registry = build_fake_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        result = await server.call_tool(
            "execute_tool",
            {"id": "fake_sponsor_tool", "args": {"value": 1}},
        )
        assert not result.is_error
        assert result.structured_content["locked"] is True

    asyncio.run(run())


def test_execute_tool_unknown_id_returns_error_with_recovery_hint() -> None:
    registry = build_fake_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        result = await server.call_tool("execute_tool", {"id": "no_such_tool", "args": {}})
        assert result.is_error
        assert "search_tools" in result.content[0].text  # type: ignore[union-attr]

    asyncio.run(run())


def test_execute_tool_on_tool_that_raises_is_caught_as_error_not_propagated() -> None:
    registry = build_fake_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        result = await server.call_tool(
            "execute_tool", {"id": "fake_broken_tool", "args": {"value": 1}}
        )
        assert result.is_error

    asyncio.run(run())


def test_immediate_tool_call_via_dedicated_tool_name_round_trips() -> None:
    registry = build_fake_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        result = await server.call_tool("fake_immediate", {"value": 21})
        assert not result.is_error
        assert result.structured_content == {"doubled": 42}

    asyncio.run(run())


def test_resources_are_registered() -> None:
    registry = build_fake_registry()
    server = build_mcp_server(registry)

    async def run() -> None:
        templates = await server.list_resource_templates()
        assert any(t.uri_template == "canonical://{symbol}" for t in templates)
        direct_resources = await server.list_resources()
        assert any(str(r.uri) == "health://sources" for r in direct_resources)

    asyncio.run(run())


def test_http_app_builds_with_host_origin_protection() -> None:
    registry = build_fake_registry()
    server = build_mcp_server(registry)
    app = build_http_app(server)
    assert app is not None
