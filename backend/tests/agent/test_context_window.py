"""`compact` — token-budget-triggered transcript compaction that preserves canonical
facts/caveats verbatim and never splits an assistant tool-call from its tool-result."""

from __future__ import annotations

from agent.context_window import compact, needs_compaction
from llm.base import Message, MessageRole, ToolCall, ToolResultPart
from tests.llm.stubs import StubLLMClient


def test_needs_compaction_pure_predicate() -> None:
    assert needs_compaction(context_tokens=100, window=100, reserve=10) is True
    assert needs_compaction(context_tokens=50, window=100, reserve=10) is False


async def test_compact_is_a_no_op_below_the_budget() -> None:
    llm = StubLLMClient()
    messages = [
        Message(role=MessageRole.SYSTEM, content="governance"),
        Message(role=MessageRole.USER, content="hi"),
    ]

    result = await compact(messages, llm, window=1_000_000, reserve=100)

    assert result == messages


async def test_compact_preserves_canonical_facts_and_caveats_when_forced() -> None:
    """A tiny window forces compaction; every line carrying a caveat/fact marker in the
    compacted-away segment must survive verbatim in the resulting compaction entry."""
    llm = StubLLMClient()
    messages = [
        Message(role=MessageRole.SYSTEM, content="governance system prompt"),
        Message(role=MessageRole.USER, content="tell me about 2330"),
        Message(
            role=MessageRole.ASSISTANT,
            content="fact: 2330 closed at 600 on 2026-08-01. currentness caveat: EOD data only.",
        ),
        Message(role=MessageRole.USER, content="and dividends?"),
        Message(
            role=MessageRole.ASSISTANT,
            content="fact: last dividend was 2.75 per share.",
        ),
        Message(role=MessageRole.USER, content="thanks, one more question"),
        Message(role=MessageRole.ASSISTANT, content="sure, go ahead"),
        Message(role=MessageRole.USER, content="what about chip flows"),
    ]

    # Force compaction regardless of the stub's cheap token estimate.
    result = await compact(messages, llm, window=1, reserve=0, keep_recent=2)

    assert result[0].role is MessageRole.SYSTEM
    assert result[0].content == "governance system prompt"
    compaction_message = result[1]
    assert compaction_message.role is MessageRole.SYSTEM
    assert compaction_message.content is not None
    assert "2330 closed at 600" in compaction_message.content
    assert "currentness caveat: EOD data only" in compaction_message.content
    assert "last dividend was 2.75 per share" in compaction_message.content
    # the recent tail is preserved verbatim, untouched.
    assert result[-1].content == "what about chip flows"


async def test_compact_never_splits_a_tool_call_from_its_result() -> None:
    """The chosen split point must never land between an assistant tool-call message and the
    tool-result message answering it."""
    llm = StubLLMClient()
    tool_call_msg = Message(
        role=MessageRole.ASSISTANT,
        content=None,
        tool_calls=(ToolCall(id="1", name="get_price_quote", arguments={}),),
    )
    tool_result_msg = Message(
        role=MessageRole.TOOL,
        tool_results=(ToolResultPart(tool_call_id="1", content="ok"),),
    )
    messages = [
        Message(role=MessageRole.SYSTEM, content="governance"),
        Message(role=MessageRole.USER, content="q1"),
        Message(role=MessageRole.ASSISTANT, content="a1"),
        Message(role=MessageRole.USER, content="q2 fact: canonical price is 600"),
        tool_call_msg,
        tool_result_msg,
    ]

    result = await compact(messages, llm, window=1, reserve=0, keep_recent=1)

    # tool_call_msg and tool_result_msg must appear adjacently, never separated by the split.
    joined = list(result)
    for index, message in enumerate(joined[:-1]):
        if message.role is MessageRole.ASSISTANT and message.tool_calls:
            assert joined[index + 1].role is MessageRole.TOOL


async def test_compact_never_touches_the_leading_system_prompt() -> None:
    llm = StubLLMClient()
    messages = [
        Message(role=MessageRole.SYSTEM, content="the one true governance prompt"),
        *[Message(role=MessageRole.USER, content=f"message {i}") for i in range(10)],
    ]

    result = await compact(messages, llm, window=1, reserve=0, keep_recent=2)

    assert result[0].content == "the one true governance prompt"
