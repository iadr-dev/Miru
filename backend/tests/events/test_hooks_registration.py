"""Proves the dependency-inverted hook-registration seam actually works end to end: a
handler built in `events/` (an interaction-layer package) fires at `agent.loop.AgentLoop`'s
real `post_tool`/`pre_llm` dispatch points, without `agent/` ever importing `events/`."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from agent.loop import AgentLoop
from events.bus import InMemoryEventBus
from events.hooks import EventBusPostToolHook, MemoryRecallPreLLMHook
from llm.base import MessageRole
from tests.agent.conftest import EchoTool
from tests.llm.stubs import StubLLMClient, make_text_response, make_tool_call_response
from tools.base import ToolContext


def _agent_module_imports() -> set[str]:
    agent_dir = Path(__file__).resolve().parents[2] / "src" / "agent"
    imported: set[str] = set()
    for path in agent_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    return imported


def test_agent_package_never_imports_events_package() -> None:
    """The static layering proof: `agent/` (runtime layer) has no import edge to `events/`
    (interaction layer) anywhere in its source tree — the dependency-inversion claim isn't
    just true at the one call site exercised below, it holds for the whole package."""
    assert "events" not in _agent_module_imports()


async def test_post_tool_hook_built_in_events_fires_from_the_real_loop_dispatch() -> None:
    bus = InMemoryEventBus()
    hook = EventBusPostToolHook(bus, turn_id="turn-1")
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1", name="echo_tool", arguments={"text": "hi"}
            ),
            make_text_response("done"),
        ]
    )
    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=4, post_tool_hooks=[hook])

    async def collect() -> list[str]:
        async for event in bus.subscribe(["tool.result"]):
            return [event.kind]
        return []

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await loop.run_turn("echo hi", tool_context=ToolContext())
    result = await task

    assert result == ["tool.result"]


async def test_pre_llm_hook_injects_memory_block_into_the_real_loop_messages() -> None:
    async def provide_block() -> str | None:
        return "Recalled background context: watchlist 'core' = [2330]"

    hook = MemoryRecallPreLLMHook(provide_block)
    llm = StubLLMClient(script=[make_text_response("Here you go.")])
    loop = AgentLoop(llm, tools=[], max_steps=2, pre_llm_hooks=[hook])

    await loop.run_turn("what's on my watchlist?", tool_context=ToolContext())

    sent_messages, _tools = llm.calls[0]
    assert any(
        message.role is MessageRole.SYSTEM
        and message.content is not None
        and "Recalled background context" in message.content
        for message in sent_messages
    )


async def test_pre_llm_hook_never_removes_the_governance_system_prefix() -> None:
    async def provide_block() -> str | None:
        return "some memory block"

    hook = MemoryRecallPreLLMHook(provide_block)
    llm = StubLLMClient(script=[make_text_response("ok")])
    loop = AgentLoop(llm, tools=[], max_steps=2, pre_llm_hooks=[hook])

    await loop.run_turn("hi", tool_context=ToolContext())

    sent_messages, _tools = llm.calls[0]
    assert sent_messages[0].role is MessageRole.SYSTEM
    assert "governed" in (sent_messages[0].content or "").lower()


async def test_pre_llm_hook_only_injects_once_per_turn() -> None:
    calls = 0

    async def provide_block() -> str | None:
        nonlocal calls
        calls += 1
        return "memory block"

    hook = MemoryRecallPreLLMHook(provide_block)
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1", name="echo_tool", arguments={"text": "x"}
            ),
            make_text_response("done"),
        ]
    )
    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=4, pre_llm_hooks=[hook])

    await loop.run_turn("hi", tool_context=ToolContext())

    assert calls == 1
