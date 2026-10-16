"""Claude-client constraints checked against the REAL tool registry, over HTTP, with the official
client: tool names, JSON Schema validity (no dangling `$ref`), payload size, deterministic order,
and that a tool added to the registry later is exposed with no change to this package."""

from __future__ import annotations

from mcp._vendor import bootstrap_vendor_sdk

bootstrap_vendor_sdk()

import json  # noqa: E402
import re  # noqa: E402

from jsonschema import Draft202012Validator  # noqa: E402

from mcp.app import build_mcp_server  # noqa: E402
from tests.mcp.conftest import FakeTool, build_fake_registry, build_real_registry  # noqa: E402
from tests.mcp.connector_support import build_app, official_session  # noqa: E402
from tests.mcp.live_server import serve  # noqa: E402

TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
# tools/list is sent into the model's context on every connector turn; keep it small. The long
# tail is behind search_tools precisely so this stays low (measured ~7 KB with 5 core tools).
MAX_LIST_BYTES = 40_000


def _unresolved_refs(node: object, root: dict[str, object]) -> list[str]:
    missing: list[str] = []
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            target: object = root
            for part in ref.removeprefix("#/").split("/"):
                target = target.get(part) if isinstance(target, dict) else None
            if target is None:
                missing.append(ref)
        for value in node.values():
            missing.extend(_unresolved_refs(value, root))
    elif isinstance(node, list):
        for item in node:
            missing.extend(_unresolved_refs(item, root))
    return missing


async def test_listed_tools_meet_claude_client_constraints() -> None:
    async with serve(build_app(build_real_registry())) as server, official_session(server) as s:
        listing = await s.list_tools()
        tools = listing.tools
        assert len(tools) >= 7
        assert [t.name for t in tools] == sorted(t.name for t in tools)
        for tool in tools:
            assert TOOL_NAME.fullmatch(tool.name), tool.name
            assert tool.title, tool.name
            assert tool.description, tool.name
            assert tool.annotations is not None
            assert tool.annotations.readOnlyHint is True
            assert tool.inputSchema["type"] == "object", tool.name
            Draft202012Validator.check_schema(tool.inputSchema)
            assert _unresolved_refs(tool.inputSchema, tool.inputSchema) == [], tool.name
            if tool.outputSchema is not None:
                assert tool.outputSchema["type"] == "object", tool.name
                Draft202012Validator.check_schema(tool.outputSchema)
                assert _unresolved_refs(tool.outputSchema, tool.outputSchema) == [], tool.name
        payload = json.dumps([t.model_dump(mode="json") for t in tools])
        assert len(payload.encode()) < MAX_LIST_BYTES


async def test_tool_order_is_identical_across_independent_servers() -> None:
    """Over HTTP from one server, and in-process from a second one built from a separately built
    registry. (Several live servers in sequence in one test process stop answering after the
    second or third: a test-process limit, not a server one, so the second listing skips HTTP.)"""
    async with serve(build_app(build_real_registry())) as server, official_session(server) as s:
        over_http = [t.name for t in (await s.list_tools()).tools]
    in_process = [t.name for t in await build_mcp_server(build_real_registry()).list_tools()]
    assert over_http == in_process
    assert over_http == sorted(over_http)


async def test_every_registry_tool_is_reachable_with_a_valid_name_and_schema() -> None:
    registry = build_real_registry()
    for tool in registry.all_tools():
        assert TOOL_NAME.fullmatch(tool.name), tool.name
        schema = tool.input_model.model_json_schema()
        Draft202012Validator.check_schema(schema)
        assert _unresolved_refs(schema, schema) == [], tool.name


async def test_real_search_results_include_a_usable_argument_schema_and_data_class() -> None:
    async with serve(build_app(build_real_registry())) as server, official_session(server) as s:
        found = await s.call_tool("search_tools", {"intent": "broker branch chip flows"})
        matches = found.structuredContent["matches"]  # type: ignore[index]
        chip = next(m for m in matches if m["name"] == "get_chip_flows")
        assert chip["data_class"] in {"public", "sponsor"}
        assert chip["input_schema"]["type"] == "object"
        assert chip["input_schema"]["properties"]


async def test_real_execute_tool_runs_over_http() -> None:
    async with serve(build_app(build_real_registry())) as server, official_session(server) as s:
        result = await s.call_tool("execute_tool", {"id": "get_source_matrix", "args": {}})
        assert not result.isError
        assert "entries" in result.structuredContent  # type: ignore[operator]


async def test_a_tool_registered_later_is_exposed_without_touching_the_mcp_package() -> None:
    """The FRED macro tools (get_us_macro_dashboard, ...) arrive as ordinary registry entries:
    a deferred one is searchable and runnable, an immediate one is listed directly."""
    registry = build_fake_registry()
    registry.register(
        FakeTool(
            "get_us_macro_dashboard", "US macro dashboard: CPI, unemployment, Fed funds rate."
        ),
        immediate=False,
    )
    registry.register(FakeTool("get_us_macro_series", "One FRED macro series."), immediate=True)
    async with serve(build_app(registry)) as server, official_session(server) as s:
        listed = {t.name for t in (await s.list_tools()).tools}
        assert "get_us_macro_series" in listed
        assert "get_us_macro_dashboard" not in listed
        found = await s.call_tool("search_tools", {"intent": "US inflation CPI unemployment"})
        names = [m["name"] for m in found.structuredContent["matches"]]  # type: ignore[index]
        assert names[0] == "get_us_macro_dashboard"
        ran = await s.call_tool(
            "execute_tool", {"id": "get_us_macro_dashboard", "args": {"value": 1}}
        )
        assert not ran.isError
