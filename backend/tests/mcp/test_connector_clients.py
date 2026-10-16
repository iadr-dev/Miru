"""Drive the real MCP app, over real HTTP, with the two real clients a Claude surface is built
from: the official `mcp` SDK client (what Claude Code / the Python SDKs use) and
`fastmcp.Client`. Nothing about the client side is mocked."""

from __future__ import annotations

from mcp._vendor import bootstrap_vendor_sdk

bootstrap_vendor_sdk()

import json  # noqa: E402
import re  # noqa: E402

import pytest  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402
from mcp.shared.exceptions import McpError  # noqa: E402
from pydantic import AnyUrl  # noqa: E402

from mcp.app import SERVER_INSTRUCTIONS  # noqa: E402
from schemas.enums import DataClass  # noqa: E402
from tests.mcp.conftest import FakeTool, build_fake_registry, build_real_registry  # noqa: E402
from tests.mcp.connector_support import build_app, fastmcp_client, official_session  # noqa: E402
from tests.mcp.live_server import serve  # noqa: E402
from tools.registry import ToolRegistry  # noqa: E402

TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _registry_with_immediate_sponsor() -> ToolRegistry:
    registry = build_fake_registry()
    registry.register(
        FakeTool("fake_sponsor_immediate", "Sponsor core tool.", data_class=DataClass.SPONSOR),
        immediate=True,
    )
    return registry


async def test_official_client_initialize_lists_and_calls() -> None:
    async with (
        serve(build_app(build_fake_registry())) as server,
        official_session(server) as session,
    ):
        tools = (await session.list_tools()).tools
        names = [t.name for t in tools]
        assert names == sorted(names)
        assert {"fake_immediate", "search_tools", "execute_tool"} == set(names)
        assert all(t.title for t in tools)

        called = await session.call_tool("fake_immediate", {"value": 21})
        assert not called.isError
        assert called.structuredContent == {"doubled": 42}
        assert json.loads(called.content[0].text) == {"doubled": 42}  # type: ignore[union-attr]


async def test_official_client_search_then_execute_deferred_tool() -> None:
    async with serve(build_app(build_fake_registry())) as server, official_session(server) as s:
        found = await s.call_tool("search_tools", {"intent": "chip flows"})
        match = found.structuredContent["matches"][0]  # type: ignore[index]
        assert match["name"] == "fake_deferred_chip_flows"
        assert match["input_schema"]["properties"]["value"]["type"] == "integer"

        ran = await s.call_tool("execute_tool", {"id": match["name"], "args": {"value": 5}})
        assert not ran.isError
        assert ran.structuredContent == {"doubled": 10}


async def test_initialize_carries_instructions_and_a_non_trading_rule() -> None:
    async with (
        serve(build_app(build_fake_registry())) as server,
        official_session(server) as session,
    ):
        init = await session.initialize()
        assert init.instructions == SERVER_INSTRUCTIONS
        assert "NOT trading advice" in (init.instructions or "")
        assert "masked teaser" in (init.instructions or "")
        assert init.serverInfo.name == "miru"


async def test_fastmcp_client_lists_and_calls() -> None:
    async with serve(build_app(build_fake_registry())) as server, fastmcp_client(server) as client:
        tools = await client.list_tools()
        assert [t.name for t in tools] == sorted(t.name for t in tools)
        assert all(t.title for t in tools)
        result = await client.call_tool("fake_immediate", {"value": 4})
        assert result.structured_content == {"doubled": 8}
        executed = await client.call_tool(
            "execute_tool", {"id": "fake_deferred_chip_flows", "args": {"value": 1}}
        )
        assert executed.structured_content == {"doubled": 2}


async def test_execute_tool_declares_an_object_output_schema() -> None:
    async with serve(build_app(build_fake_registry())) as server, official_session(server) as s:
        execute = next(t for t in (await s.list_tools()).tools if t.name == "execute_tool")
        assert execute.outputSchema is not None
        assert execute.outputSchema["type"] == "object"


async def test_sponsor_tool_teaser_validates_against_its_declared_output_schema() -> None:
    """The official client validates structuredContent against outputSchema. An anonymous caller
    gets a MaskedTeaser from a sponsor tool; the schema must therefore admit it."""
    async with (
        serve(build_app(_registry_with_immediate_sponsor())) as server,
        official_session(server) as s,
    ):
        result = await s.call_tool("fake_sponsor_immediate", {"value": 1})
        assert not result.isError
        assert result.structuredContent["locked"] is True  # type: ignore[index]
        assert result.structuredContent["required_tier"] == "premium"  # type: ignore[index]


async def test_bad_arguments_are_a_tool_error_with_a_recovery_hint() -> None:
    async with serve(build_app(build_fake_registry())) as server, official_session(server) as s:
        bad = await s.call_tool("fake_immediate", {"value": -1})
        assert bad.isError
        assert "Invalid arguments" in bad.content[0].text  # type: ignore[union-attr]
        assert "retry" in bad.content[0].text  # type: ignore[union-attr]

        via_execute = await s.call_tool(
            "execute_tool", {"id": "fake_deferred_chip_flows", "args": {"value": "x"}}
        )
        assert via_execute.isError


async def test_unknown_execute_tool_id_suggests_close_names() -> None:
    async with serve(build_app(build_fake_registry())) as server, official_session(server) as s:
        result = await s.call_tool("execute_tool", {"id": "chip_flows", "args": {}})
        assert result.isError
        text = result.content[0].text  # type: ignore[union-attr]
        assert "fake_deferred_chip_flows" in text
        assert "search_tools" in text


async def test_a_tool_that_raises_is_an_error_result_not_a_dropped_connection() -> None:
    async with serve(build_app(build_fake_registry())) as server, official_session(server) as s:
        broken = await s.call_tool("execute_tool", {"id": "fake_broken_tool", "args": {"value": 1}})
        assert broken.isError
        # ...and the same session keeps working afterwards.
        ok = await s.call_tool("fake_immediate", {"value": 1})
        assert not ok.isError


async def test_unknown_mcp_tool_name_is_rejected_cleanly() -> None:
    async with serve(build_app(build_fake_registry())) as server, official_session(server) as s:
        try:
            result = await s.call_tool("no_such_tool", {})
        except McpError:
            return  # JSON-RPC error: acceptable
        assert result.isError

    async with serve(build_app(build_fake_registry())) as server, fastmcp_client(server) as client:
        with pytest.raises(ToolError):
            await client.call_tool("no_such_tool", {})


async def test_resources_list_and_read_over_http() -> None:
    registry = build_real_registry()
    async with serve(build_app(registry)) as server, official_session(server) as s:
        resources = (await s.list_resources()).resources
        assert [str(r.uri) for r in resources] == ["health://sources"]
        templates = (await s.list_resource_templates()).resourceTemplates
        assert [t.uriTemplate for t in templates] == ["canonical://{symbol}"]
        assert all(r.title for r in resources)

        read = await s.read_resource(AnyUrl("health://sources"))
        assert read.contents
        assert read.contents[0].mimeType == "application/json"
        json.loads(read.contents[0].text)  # type: ignore[union-attr]

    async with serve(build_app(registry)) as server, fastmcp_client(server) as client:
        contents = await client.read_resource("health://sources")
        json.loads(contents[0].text)  # type: ignore[union-attr]


async def test_stateful_mode_also_works_with_the_official_client() -> None:
    """Claude Code and local runs may talk to a stateful (Mcp-Session-Id) deployment."""
    app = build_app(build_fake_registry(), stateless_http=False)
    async with serve(app) as server, official_session(server) as s:
        assert (await s.call_tool("fake_immediate", {"value": 2})).structuredContent == {
            "doubled": 4
        }
