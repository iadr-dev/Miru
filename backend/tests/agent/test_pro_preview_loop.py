"""The agent loop dispatches the Pro-preview tools through `dispatch_tool` with the caller's tier,
so a Free/Premium turn hands the model the limited result plus the note it can relay (the full
view is part of the Pro plan), while a Pro turn hands it everything."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.loop import AgentLoop
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys
from schemas.enums import Tier
from tests.llm.stubs import StubLLMClient, make_text_response, make_tool_call_response
from tests.tools.test_theme_previews import _seed
from tools.base import Tool, ToolContext
from tools.registry import ToolRegistry
from tools.theme_tools import build_registration_table


@pytest.fixture
async def tools() -> AsyncIterator[list[Tool]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed(factory)
    registry = ToolRegistry()
    for tool, immediate in build_registration_table(factory):
        registry.register(tool, immediate=immediate)
    yield [
        t
        for name in ("get_supply_chain_edges", "get_theme_members")
        if (t := registry.get_tool(name))
    ]
    await engine.dispose()


async def _tool_result(
    tools: list[Tool], name: str, arguments: dict[str, object], tier: Tier
) -> str:
    llm = StubLLMClient(
        script=[
            make_tool_call_response(tool_call_id="call-1", name=name, arguments=arguments),
            make_text_response("Here is what the data shows."),
        ]
    )
    loop = AgentLoop(llm, tools=tools, max_steps=4)
    result = await loop.run_turn("look it up", tool_context=ToolContext(user_id="u", tier=tier))
    return result.steps[0].tool_messages[0].tool_results[0].content


@pytest.mark.parametrize("tier", [Tier.FREE, Tier.PREMIUM])
async def test_non_pro_turn_gets_the_preview_and_the_note(tools: list[Tool], tier: Tier) -> None:
    edges = await _tool_result(
        tools, "get_supply_chain_edges", {"id": "9001", "market": "TW"}, tier
    )
    assert '"locked":true' in edges
    assert '"sources_locked":true' in edges
    assert "Pro plan" in edges
    assert ".example/" not in edges  # the source links are withheld

    members = await _tool_result(tools, "get_theme_members", {"theme_key": "wave"}, tier)
    assert '"locked":true' in members
    assert "9103" not in members  # the 4th upstream member is withheld
    assert "Pro plan" in members


async def test_pro_turn_gets_everything(tools: list[Tool]) -> None:
    edges = await _tool_result(
        tools, "get_supply_chain_edges", {"id": "9001", "market": "TW"}, Tier.PRO
    )
    assert '"locked":false' in edges
    assert ".example/" in edges
    members = await _tool_result(tools, "get_theme_members", {"theme_key": "wave"}, Tier.PRO)
    assert '"locked":false' in members
    assert "9103" in members
