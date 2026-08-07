"""Tests for `StubLLMClient` itself — the deterministic scripted-response test double."""

from __future__ import annotations

import pytest

from llm.base import Message, MessageRole, StopReason
from tests.llm.stubs import StubLLMClient, make_text_response, make_tool_call_response


async def test_chat_replays_script_in_order() -> None:
    stub = StubLLMClient(
        script=[
            make_tool_call_response(tool_call_id="1", name="get_x", arguments={}),
            make_text_response("final answer"),
        ]
    )
    messages = [Message(role=MessageRole.USER, content="hi")]

    first = await stub.chat(messages)
    assert first.stop_reason is StopReason.TOOL_USE
    assert first.tool_calls[0].name == "get_x"

    second = await stub.chat(messages)
    assert second.text == "final answer"


async def test_chat_records_calls() -> None:
    stub = StubLLMClient(script=[make_text_response("hi")])
    messages = [Message(role=MessageRole.USER, content="hello")]
    await stub.chat(messages, tools=None)
    assert stub.calls == [(messages, None)]


async def test_chat_raises_when_script_exhausted() -> None:
    stub = StubLLMClient(script=[make_text_response("hi")])
    await stub.chat([])
    with pytest.raises(AssertionError, match="script only has"):
        await stub.chat([])


async def test_stream_yields_text_then_done() -> None:
    stub = StubLLMClient(script=[make_text_response("hi there")])
    events = [event async for event in stub.stream([])]
    assert events[0].type == "text_delta"
    assert events[0].text == "hi there"  # type: ignore[union-attr]
    assert events[-1].type == "done"


async def test_reset_rewinds_script_and_calls() -> None:
    stub = StubLLMClient(script=[make_text_response("hi")])
    await stub.chat([])
    stub.reset()
    assert stub.calls == []
    await stub.chat([])  # does not raise — script replays from the start


async def test_count_tokens_heuristic() -> None:
    stub = StubLLMClient(script=[])
    messages = [Message(role=MessageRole.USER, content="a" * 40)]
    assert await stub.count_tokens(messages) == 10
