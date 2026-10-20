"""`AnthropicClient` tests — the SDK is mocked (`AsyncMock` on `messages.create`/
`.count_tokens`/`.stream`), never a real network call in the default profile.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any
from unittest.mock import AsyncMock

import pytest
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStreamEvent,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolUseBlock,
    Usage,
    WebSearchResultBlock,
    WebSearchToolResultBlock,
)
from anthropic.types import Message as AnthropicMessage
from anthropic.types.message_delta_usage import MessageDeltaUsage
from anthropic.types.message_tokens_count import MessageTokensCount
from anthropic.types.raw_message_delta_event import Delta as StopDelta
from structlog.testing import capture_logs

from llm.anthropic_client import AnthropicClient
from llm.base import (
    ImagePart,
    LLMRefusalError,
    Message,
    MessageRole,
    StopReason,
    ToolCall,
    ToolResultPart,
    ToolSpec,
)


class _FakeMessageStream:
    """A minimal stand-in for `anthropic`'s `MessageStream` async context manager/iterator —
    enough surface for `AnthropicClient.stream()` (`async with ... as stream: async for
    event in stream`), without depending on the SDK's real network-backed implementation."""

    def __init__(self, events: Sequence[RawMessageStreamEvent]) -> None:
        self._events = events

    async def __aenter__(self) -> _FakeMessageStream:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[RawMessageStreamEvent]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[RawMessageStreamEvent]:
        for event in self._events:
            yield event


def _client() -> AnthropicClient:
    return AnthropicClient(model_id="claude-sonnet-5", api_key="test-key")


def _sdk_message(
    *,
    content: list[Any],
    stop_reason: str = "end_turn",
    input_tokens: int = 5,
    output_tokens: int = 3,
) -> AnthropicMessage:
    return AnthropicMessage(
        id="msg_1",
        content=content,
        model="claude-sonnet-5",
        role="assistant",
        stop_reason=stop_reason,  # type: ignore[arg-type]
        stop_sequence=None,
        type="message",
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def test_construction_performs_no_network_call() -> None:
    client = _client()
    assert client.provider_id == "anthropic"
    assert client.model_id == "claude-sonnet-5"
    assert client.capabilities.native_web_search is True


async def test_chat_normalizes_text_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    sdk_response = _sdk_message(content=[TextBlock(type="text", text="hello", citations=None)])
    monkeypatch.setattr(client._client.messages, "create", AsyncMock(return_value=sdk_response))

    response = await client.chat([Message(role=MessageRole.USER, content="hi")])

    assert response.text == "hello"
    assert response.tool_calls == ()
    assert response.stop_reason is StopReason.END_TURN
    assert response.usage.input_tokens == 5
    assert response.usage.output_tokens == 3
    assert response.usage.total_tokens == 8


async def test_chat_sends_image_block_alongside_text(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    create_mock = AsyncMock(
        return_value=_sdk_message(content=[TextBlock(type="text", text="ok", citations=None)])
    )
    monkeypatch.setattr(client._client.messages, "create", create_mock)

    await client.chat(
        [
            Message(
                role=MessageRole.USER,
                content="what's in this chart?",
                images=(ImagePart(media_type="image/png", data="Zm9v"),),
            )
        ]
    )

    messages_param = create_mock.call_args.kwargs["messages"]
    content = messages_param[0]["content"]
    assert content[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "Zm9v"},
    }
    # The last block of the last message carries the conversation's cache breakpoint.
    assert content[1] == {
        "type": "text",
        "text": "what's in this chart?",
        "cache_control": {"type": "ephemeral"},
    }


async def test_chat_normalizes_tool_call_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    sdk_response = _sdk_message(
        content=[
            ToolUseBlock(id="call_1", input={"symbol": "2330"}, name="get_x", type="tool_use")
        ],
        stop_reason="tool_use",
    )
    monkeypatch.setattr(client._client.messages, "create", AsyncMock(return_value=sdk_response))

    response = await client.chat(
        [Message(role=MessageRole.USER, content="hi")],
        tools=[ToolSpec(name="get_x", description="d", parameters={"type": "object"})],
    )

    assert response.text is None
    assert response.stop_reason is StopReason.TOOL_USE
    expected = ToolCall(id="call_1", name="get_x", arguments={"symbol": "2330"})
    assert response.tool_calls[0] == expected


async def test_chat_puts_system_messages_in_system_param(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    create_mock = AsyncMock(
        return_value=_sdk_message(content=[TextBlock(type="text", text="ok", citations=None)])
    )
    monkeypatch.setattr(client._client.messages, "create", create_mock)

    await client.chat(
        [
            Message(role=MessageRole.SYSTEM, content="be caveated"),
            Message(role=MessageRole.USER, content="hi"),
        ]
    )

    _, kwargs = create_mock.call_args
    # `system` is rendered as a cacheable content-block list, not a plain string — see
    # `anthropic_client._system_param` (a plain-string `system` can't carry `cache_control`).
    assert kwargs["system"] == [
        {"type": "text", "text": "be caveated", "cache_control": {"type": "ephemeral"}}
    ]
    assert len(kwargs["messages"]) == 1
    assert kwargs["messages"][0]["role"] == "user"


async def test_chat_converts_tool_result_message_to_user_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    create_mock = AsyncMock(
        return_value=_sdk_message(content=[TextBlock(type="text", text="ok", citations=None)])
    )
    monkeypatch.setattr(client._client.messages, "create", create_mock)

    await client.chat(
        [
            Message(
                role=MessageRole.TOOL,
                tool_results=(ToolResultPart(tool_call_id="call_1", content="42"),),
            )
        ]
    )

    _, kwargs = create_mock.call_args
    sent = kwargs["messages"][0]
    assert sent["role"] == "user"
    assert sent["content"][0]["type"] == "tool_result"
    assert sent["content"][0]["tool_use_id"] == "call_1"


async def test_chat_never_logs_api_key_on_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = _client()
    monkeypatch.setattr(
        client._client.messages, "create", AsyncMock(side_effect=RuntimeError("boom"))
    )

    messages = [Message(role=MessageRole.USER, content="hi")]
    with pytest.raises(RuntimeError):
        await client.chat(messages)

    assert "test-key" not in caplog.text


async def test_stream_assembles_text_and_tool_call_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    events: list[RawMessageStreamEvent] = [
        RawMessageStartEvent(
            message=_sdk_message(content=[], input_tokens=5, output_tokens=0),
            type="message_start",
        ),
        RawContentBlockStartEvent(
            content_block=TextBlock(type="text", text="", citations=None),
            index=0,
            type="content_block_start",
        ),
        RawContentBlockDeltaEvent(
            delta=TextDelta(text="hel", type="text_delta"), index=0, type="content_block_delta"
        ),
        RawContentBlockDeltaEvent(
            delta=TextDelta(text="lo", type="text_delta"), index=0, type="content_block_delta"
        ),
        RawContentBlockStartEvent(
            content_block=ToolUseBlock(id="call_1", input={}, name="get_x", type="tool_use"),
            index=1,
            type="content_block_start",
        ),
        RawContentBlockDeltaEvent(
            delta=InputJSONDelta(partial_json='{"a":1}', type="input_json_delta"),
            index=1,
            type="content_block_delta",
        ),
        RawMessageDeltaEvent(
            delta=StopDelta(stop_reason="tool_use", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=3),
            type="message_delta",
        ),
    ]
    monkeypatch.setattr(
        client._client.messages, "stream", lambda **_kwargs: _FakeMessageStream(events)
    )

    collected = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    text_events = [e for e in collected if e.type == "text_delta"]
    assert "".join(e.text for e in text_events) == "hello"  # type: ignore[union-attr]

    tool_call_events = [e for e in collected if e.type == "tool_call"]
    assert tool_call_events[0].tool_call == ToolCall(id="call_1", name="get_x", arguments={"a": 1})  # type: ignore[union-attr]

    done = collected[-1]
    assert done.type == "done"
    assert done.stop_reason is StopReason.TOOL_USE  # type: ignore[union-attr]
    assert done.usage.input_tokens == 5  # type: ignore[union-attr]
    assert done.usage.output_tokens == 3  # type: ignore[union-attr]


async def test_stream_emits_thinking_delta_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """`content_block_start` for a `ThinkingBlock` tracks nothing (no id, unlike `tool_use`);
    the actual reasoning text arrives only via `ThinkingDelta` (`delta.type ==
    "thinking_delta"`, field `delta.thinking`) — verified directly against the installed SDK's
    `RawContentBlockDeltaEvent`/`ThinkingDelta` types, not guessed."""
    client = _client()
    events: list[RawMessageStreamEvent] = [
        RawMessageStartEvent(
            message=_sdk_message(content=[], input_tokens=5, output_tokens=0),
            type="message_start",
        ),
        RawContentBlockStartEvent(
            content_block=ThinkingBlock(type="thinking", thinking="", signature=""),
            index=0,
            type="content_block_start",
        ),
        RawContentBlockDeltaEvent(
            delta=ThinkingDelta(thinking="pondering", type="thinking_delta"),
            index=0,
            type="content_block_delta",
        ),
        RawContentBlockDeltaEvent(
            delta=ThinkingDelta(thinking=" further", type="thinking_delta"),
            index=0,
            type="content_block_delta",
        ),
        RawContentBlockStartEvent(
            content_block=TextBlock(type="text", text="", citations=None),
            index=1,
            type="content_block_start",
        ),
        RawContentBlockDeltaEvent(
            delta=TextDelta(text="final answer", type="text_delta"),
            index=1,
            type="content_block_delta",
        ),
        RawMessageDeltaEvent(
            delta=StopDelta(stop_reason="end_turn", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=3),
            type="message_delta",
        ),
    ]
    monkeypatch.setattr(
        client._client.messages, "stream", lambda **_kwargs: _FakeMessageStream(events)
    )

    collected = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    thinking_events = [e for e in collected if e.type == "thinking_delta"]
    assert "".join(e.text for e in thinking_events) == "pondering further"  # type: ignore[union-attr]

    text_events = [e for e in collected if e.type == "text_delta"]
    assert "".join(e.text for e in text_events) == "final answer"  # type: ignore[union-attr]


async def test_stream_emits_web_search_result_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `web_search_tool_result` content block arrives whole on `content_block_start` (no
    incremental assembly, unlike `tool_use`) — verified live against a real streamed response
    (see the task's live-verification pass); this test mirrors that exact shape via the real
    SDK types rather than a hand-rolled stub."""
    client = _client()
    events: list[RawMessageStreamEvent] = [
        RawMessageStartEvent(
            message=_sdk_message(content=[], input_tokens=5, output_tokens=0),
            type="message_start",
        ),
        RawContentBlockStartEvent(
            content_block=WebSearchToolResultBlock(
                content=[
                    WebSearchResultBlock(
                        encrypted_content="enc",
                        page_age="2 days ago",
                        title="NVIDIA (NVDA) Stock price today - quote & chart",
                        type="web_search_result",
                        url="https://www.kraken.com/stocks/nvda",
                    ),
                    WebSearchResultBlock(
                        encrypted_content="enc2",
                        page_age=None,
                        title="Nvidia stock analysis",
                        type="web_search_result",
                        url="https://ca.investing.com/news/nvidia-stock-analysis",
                    ),
                ],
                tool_use_id="srvtoolu_1",
                type="web_search_tool_result",
            ),
            index=1,
            type="content_block_start",
        ),
        RawMessageDeltaEvent(
            delta=StopDelta(stop_reason="end_turn", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=3),
            type="message_delta",
        ),
    ]
    monkeypatch.setattr(
        client._client.messages, "stream", lambda **_kwargs: _FakeMessageStream(events)
    )

    collected = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    web_search_events = [e for e in collected if e.type == "web_search_result"]
    assert len(web_search_events) == 1
    sources = web_search_events[0].sources  # type: ignore[union-attr]
    assert sources[0].url == "https://www.kraken.com/stocks/nvda"
    assert sources[0].title == "NVIDIA (NVDA) Stock price today - quote & chart"
    assert sources[1].url == "https://ca.investing.com/news/nvidia-stock-analysis"


async def test_chat_appends_native_web_search_tool_block_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    create_mock = AsyncMock(
        return_value=_sdk_message(content=[TextBlock(type="text", text="ok", citations=None)])
    )
    monkeypatch.setattr(client._client.messages, "create", create_mock)

    await client.chat(
        [Message(role=MessageRole.USER, content="hi")],
        tools=[ToolSpec(name="get_x", description="d", parameters={"type": "object"})],
        native_web_search=True,
    )

    _, kwargs = create_mock.call_args
    tools = kwargs["tools"]
    web_search_tools = [t for t in tools if t.get("type") == "web_search_20250305"]
    assert len(web_search_tools) == 1
    assert web_search_tools[0]["name"] == "web_search"
    # The last tool entry (whichever it is) carries the cache_control breakpoint so the whole
    # static tool-schema block is cached together — see `anthropic_client._tools_param`.
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}


async def test_chat_omits_native_web_search_tool_block_when_not_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    create_mock = AsyncMock(
        return_value=_sdk_message(content=[TextBlock(type="text", text="ok", citations=None)])
    )
    monkeypatch.setattr(client._client.messages, "create", create_mock)

    await client.chat(
        [Message(role=MessageRole.USER, content="hi")],
        tools=[ToolSpec(name="get_x", description="d", parameters={"type": "object"})],
    )

    _, kwargs = create_mock.call_args
    assert all(t.get("type") != "web_search_20250305" for t in kwargs["tools"])


async def test_chat_caches_the_tool_schema_block_even_without_native_web_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    create_mock = AsyncMock(
        return_value=_sdk_message(content=[TextBlock(type="text", text="ok", citations=None)])
    )
    monkeypatch.setattr(client._client.messages, "create", create_mock)

    await client.chat(
        [Message(role=MessageRole.USER, content="hi")],
        tools=[ToolSpec(name="get_x", description="d", parameters={"type": "object"})],
    )

    _, kwargs = create_mock.call_args
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_chat_sends_adaptive_thinking_for_a_supported_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()  # claude-sonnet-5 — in _EXTENDED_THINKING_MODEL_PREFIXES
    create_mock = AsyncMock(
        return_value=_sdk_message(content=[TextBlock(type="text", text="ok", citations=None)])
    )
    monkeypatch.setattr(client._client.messages, "create", create_mock)

    assert client.capabilities.extended_thinking is True

    await client.chat([Message(role=MessageRole.USER, content="hi")])

    _, kwargs = create_mock.call_args
    assert kwargs["thinking"] == {"type": "adaptive"}


async def test_chat_sends_adaptive_thinking_for_the_deployment_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`claude-sonnet-5-5` is `llm/registry.py`'s own `_DEFAULT_MODEL` — the model actually
    serving live turns in this deployment. Regression guard for the gap this test was added to
    close: it was missing from `_EXTENDED_THINKING_MODEL_PREFIXES`, so no `thinking` param was
    ever sent for it and `thinking.delta` never streamed even though the model supports
    adaptive thinking (confirmed against Anthropic's own supported-model table)."""
    client = AnthropicClient(model_id="claude-sonnet-5-5", api_key="test-key")
    create_mock = AsyncMock(
        return_value=_sdk_message(content=[TextBlock(type="text", text="ok", citations=None)])
    )
    monkeypatch.setattr(client._client.messages, "create", create_mock)

    assert client.capabilities.extended_thinking is True

    await client.chat([Message(role=MessageRole.USER, content="hi")])

    _, kwargs = create_mock.call_args
    assert kwargs["thinking"] == {"type": "adaptive"}


async def test_chat_omits_thinking_for_an_unsupported_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AnthropicClient(model_id="claude-haiku-4-5", api_key="test-key")
    create_mock = AsyncMock(
        return_value=_sdk_message(content=[TextBlock(type="text", text="ok", citations=None)])
    )
    monkeypatch.setattr(client._client.messages, "create", create_mock)

    assert client.capabilities.extended_thinking is False

    await client.chat([Message(role=MessageRole.USER, content="hi")])

    _, kwargs = create_mock.call_args
    assert "thinking" not in kwargs


async def test_count_tokens_uses_sdk_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(
        client._client.messages,
        "count_tokens",
        AsyncMock(return_value=MessageTokensCount(input_tokens=42)),
    )

    count = await client.count_tokens([Message(role=MessageRole.USER, content="hi")])

    assert count == 42


def _breakpoints(kwargs: dict[str, Any]) -> int:
    """How many `cache_control` markers the request carries (the API allows four)."""
    count = 0
    for tool in kwargs.get("tools", []):
        count += "cache_control" in tool
    for block in kwargs.get("system", []):
        count += "cache_control" in block
    for message in kwargs["messages"]:
        content = message["content"]
        if isinstance(content, list):
            count += sum("cache_control" in block for block in content)
    return count


_RECALL = Message(role=MessageRole.SYSTEM, content="remembered: likes TSMC", per_turn=True)


def _history_with_recall() -> list[Message]:
    return [
        Message(role=MessageRole.SYSTEM, content="be caveated"),
        _RECALL,
        Message(role=MessageRole.USER, content="first question"),
        Message(role=MessageRole.ASSISTANT, content="first answer"),
        Message(role=MessageRole.USER, content="second question"),
    ]


async def _sent(model_id: str, messages: list[Message], monkeypatch: pytest.MonkeyPatch) -> Any:
    client = AnthropicClient(model_id=model_id, api_key="test-key")
    create_mock = AsyncMock(
        return_value=_sdk_message(content=[TextBlock(type="text", text="ok", citations=None)])
    )
    monkeypatch.setattr(client._client.messages, "create", create_mock)
    await client.chat(messages)
    return create_mock.call_args.kwargs


async def test_the_memory_block_goes_after_the_latest_question_on_a_model_that_allows_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = await _sent("claude-sonnet-5-5", _history_with_recall(), monkeypatch)

    # The stable prompt alone is `system`, so the memory block cannot change the cached prefix.
    assert kwargs["system"][0]["text"] == "be caveated"
    roles = [m["role"] for m in kwargs["messages"]]
    assert roles == ["user", "assistant", "user", "system"]
    assert kwargs["messages"][-1]["content"] == "remembered: likes TSMC"
    # One breakpoint ends the stable history (the latest question), none sits on the memory block.
    assert "cache_control" in kwargs["messages"][2]["content"][-1]
    assert _breakpoints(kwargs) <= 4


async def test_the_memory_block_trails_the_latest_question_on_a_model_that_rejects_the_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = await _sent("claude-sonnet-4-6", _history_with_recall(), monkeypatch)

    # No `system` role in `messages` (that model returns a 400), and the system text stays stable.
    assert kwargs["system"][0]["text"] == "be caveated"
    assert [m["role"] for m in kwargs["messages"]] == ["user", "assistant", "user"]
    question, memory = kwargs["messages"][-1]["content"]
    assert question["text"] == "second question"
    assert "cache_control" in question  # the stable history ends here
    assert memory == {"type": "text", "text": "remembered: likes TSMC"}  # not cached
    assert _breakpoints(kwargs) <= 4


async def test_in_a_tool_loop_the_stable_history_and_the_newest_result_are_both_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = [
        *_history_with_recall(),
        Message(
            role=MessageRole.ASSISTANT,
            tool_calls=(ToolCall(id="call_1", name="lookup", arguments={}),),
        ),
        Message(
            role=MessageRole.TOOL,
            tool_results=(ToolResultPart(tool_call_id="call_1", content="42"),),
        ),
    ]

    kwargs = await _sent("claude-opus-5-5", history, monkeypatch)

    roles = [m["role"] for m in kwargs["messages"]]
    assert roles == ["user", "assistant", "user", "system", "assistant", "user"]
    assert "cache_control" in kwargs["messages"][2]["content"][-1]  # end of the stable history
    assert "cache_control" in kwargs["messages"][-1]["content"][-1]  # the newest tool result
    assert _breakpoints(kwargs) <= 4


async def test_with_no_memory_block_the_conversation_is_cached_up_to_the_last_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = [m for m in _history_with_recall() if not m.per_turn]

    kwargs = await _sent("claude-opus-5-5", history, monkeypatch)

    assert [m["role"] for m in kwargs["messages"]] == ["user", "assistant", "user"]
    assert "cache_control" in kwargs["messages"][-1]["content"][-1]
    assert "cache_control" not in str(kwargs["messages"][0])


async def test_chat_raises_when_the_provider_declines_instead_of_returning_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A safety classifier answers HTTP 200 with `stop_reason: "refusal"` and no content. That
    used to come back as an empty answer; it is now an error a fallback chain can act on."""
    client = AnthropicClient(model_id="claude-opus-5-5", api_key="test-key")
    refused = _sdk_message(content=[], stop_reason="refusal", output_tokens=0)
    monkeypatch.setattr(client._client.messages, "create", AsyncMock(return_value=refused))

    messages = [Message(role=MessageRole.USER, content="hi")]
    with pytest.raises(LLMRefusalError, match="claude-opus-5-5 declined"):
        await client.chat(messages)


async def test_stream_raises_when_the_provider_declines(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AnthropicClient(model_id="claude-opus-5-5", api_key="test-key")
    events: list[RawMessageStreamEvent] = [
        RawMessageStartEvent(
            message=_sdk_message(content=[], input_tokens=5, output_tokens=0), type="message_start"
        ),
        RawMessageDeltaEvent(
            delta=StopDelta(stop_reason="refusal", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=0),
            type="message_delta",
        ),
    ]
    monkeypatch.setattr(
        client._client.messages, "stream", lambda **_kwargs: _FakeMessageStream(events)
    )

    messages = [Message(role=MessageRole.USER, content="hi")]
    with pytest.raises(LLMRefusalError):
        [e async for e in client.stream(messages)]


def _diagnosed(reason: dict[str, Any] | None, *, message_id: str) -> AnthropicMessage:
    extra: dict[str, Any] = {"diagnostics": {"cache_miss_reason": reason} if reason else None}
    return AnthropicMessage(
        id=message_id,
        content=[TextBlock(type="text", text="ok", citations=None)],
        model="claude-sonnet-5-5",
        role="assistant",
        stop_reason="end_turn",
        stop_sequence=None,
        type="message",
        usage=Usage(input_tokens=5, output_tokens=3),
        **extra,
    )


async def test_cache_diagnostics_is_off_unless_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AnthropicClient(model_id="claude-sonnet-5-5", api_key="test-key")
    create_mock = AsyncMock(return_value=_diagnosed(None, message_id="msg_1"))
    monkeypatch.setattr(client._client.messages, "create", create_mock)

    await client.chat([Message(role=MessageRole.USER, content="hi")])

    assert "extra_body" not in create_mock.call_args.kwargs


async def test_cache_diagnostics_compares_each_request_with_the_previous_one_and_logs_a_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AnthropicClient(
        model_id="claude-sonnet-5-5", api_key="test-key", cache_diagnostics=True
    )
    miss = {"type": "system_changed", "cache_missed_input_tokens": 2609}
    create_mock = AsyncMock(
        side_effect=[_diagnosed(None, message_id="msg_1"), _diagnosed(miss, message_id="msg_2")]
    )
    monkeypatch.setattr(client._client.messages, "create", create_mock)

    await client.chat([Message(role=MessageRole.USER, content="first")])
    with capture_logs() as logs:
        await client.chat([Message(role=MessageRole.USER, content="second")])

    first, second = (call.kwargs["extra_body"] for call in create_mock.call_args_list)
    assert first == {"diagnostics": {"previous_message_id": None}}
    assert second == {"diagnostics": {"previous_message_id": "msg_1"}}
    assert [(e["event"], e["reason"], e["missed_input_tokens"]) for e in logs] == [
        ("llm_cache_miss", "system_changed", 2609)
    ]


async def test_a_request_that_kept_its_prefix_logs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AnthropicClient(
        model_id="claude-sonnet-5-5", api_key="test-key", cache_diagnostics=True
    )
    monkeypatch.setattr(
        client._client.messages, "create", AsyncMock(return_value=_diagnosed(None, message_id="m"))
    )

    with capture_logs() as logs:
        await client.chat([Message(role=MessageRole.USER, content="hi")])

    assert logs == []


async def test_streaming_reads_the_diagnostics_from_the_message_start_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AnthropicClient(
        model_id="claude-sonnet-5-5", api_key="test-key", cache_diagnostics=True
    )
    miss = {"type": "tools_changed", "cache_missed_input_tokens": 900}
    events: list[RawMessageStreamEvent] = [
        RawMessageStartEvent(message=_diagnosed(miss, message_id="msg_s1"), type="message_start"),
        RawMessageDeltaEvent(
            delta=StopDelta(stop_reason="end_turn", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=1),
            type="message_delta",
        ),
    ]
    seen: dict[str, Any] = {}

    def fake_stream(**kwargs: Any) -> _FakeMessageStream:
        seen.update(kwargs)
        return _FakeMessageStream(events)

    monkeypatch.setattr(client._client.messages, "stream", fake_stream)

    with capture_logs() as logs:
        [e async for e in client.stream([Message(role=MessageRole.USER, content="hi")])]

    assert seen["extra_body"] == {"diagnostics": {"previous_message_id": None}}
    assert [e["reason"] for e in logs] == ["tools_changed"]
    assert client._previous_message_id == "msg_s1"
