"""`mcp._dispatch` — the shared dispatch-to-`ToolResult` plumbing every MCP tool handler in this
package goes through. Proves: a real tool's success renders `structuredContent` + a JSON text
fallback, never `isError`; a `MaskedTeaser` (sponsor gate) renders the same way — masking is a
successful result, not an error; a tool-internal exception is caught and rendered `isError`
with a recovery hint, never propagated; bad arguments are also caught as `isError`, never a
raised `ValidationError`."""

from __future__ import annotations

import json

from mcp._dispatch import dispatch_and_render
from schemas.enums import DataClass, Tier
from tests.mcp.conftest import FakeTool
from tools.base import ToolContext


async def test_successful_dispatch_renders_structured_content_and_text_fallback() -> None:
    tool = FakeTool("double", "doubles a value")
    result = await dispatch_and_render(tool, {"value": 3}, ToolContext())

    assert not result.is_error
    assert result.structured_content == {"doubled": 6}
    assert len(result.content) == 1
    assert json.loads(result.content[0].text) == {"doubled": 6}  # type: ignore[union-attr]


async def test_masked_teaser_is_a_successful_result_not_an_error() -> None:
    tool = FakeTool("sponsor_thing", "a sponsor tool", data_class=DataClass.SPONSOR)
    result = await dispatch_and_render(tool, {"value": 1}, ToolContext(tier=Tier.FREE))

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["locked"] is True
    assert result.structured_content["tool_name"] == "sponsor_thing"


async def test_entitled_tier_gets_real_data_not_a_teaser() -> None:
    tool = FakeTool("sponsor_thing", "a sponsor tool", data_class=DataClass.SPONSOR)
    result = await dispatch_and_render(tool, {"value": 5}, ToolContext(tier=Tier.PREMIUM))

    assert not result.is_error
    assert result.structured_content == {"doubled": 10}


async def test_tool_internal_exception_is_caught_and_rendered_as_error() -> None:
    tool = FakeTool("broken", "always raises", raises=True)
    result = await dispatch_and_render(tool, {"value": 1}, ToolContext())

    assert result.is_error
    assert "broken" in result.content[0].text  # type: ignore[union-attr]


async def test_invalid_arguments_are_caught_as_error_not_raised() -> None:
    tool = FakeTool("double", "doubles a value")
    result = await dispatch_and_render(tool, {"value": "not-an-int"}, ToolContext())

    assert result.is_error
    assert "double" in result.content[0].text  # type: ignore[union-attr]
