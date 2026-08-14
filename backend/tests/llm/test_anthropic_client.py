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

from llm.anthropic_client import AnthropicClient
from llm.base import ImagePart, Message, MessageRole, StopReason, ToolCall, ToolResultPart, ToolSpec


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
    assert content[1] == {"type": "text", "text": "what's in this chart?"}


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

    with pytest.raises(RuntimeError):
        await client.chat([Message(role=MessageRole.USER, content="hi")])

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
