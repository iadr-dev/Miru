"""`OpenAIClient` tests — the SDK is mocked (`AsyncMock` on `chat.completions.create`), never
a real network call in the default profile.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.chat.chat_completion import Choice as CompletionChoice
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice
from openai.types.chat.chat_completion_chunk import (
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
    Function,
)
from openai.types.completion_usage import CompletionUsage

from llm.base import Message, MessageRole, StopReason, ToolCall, ToolResultPart, ToolSpec
from llm.openai_client import OpenAIClient


class _FakeAsyncStream:
    """A minimal stand-in for `openai`'s `AsyncStream[ChatCompletionChunk]` — enough surface
    for `OpenAIClient.stream()` (`async for chunk in response`), without depending on the
    SDK's real network-backed implementation."""

    def __init__(self, chunks: list[ChatCompletionChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[ChatCompletionChunk]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[ChatCompletionChunk]:
        for chunk in self._chunks:
            yield chunk


def _client() -> OpenAIClient:
    return OpenAIClient(model_id="gpt-4o", api_key="test-key")


def _completion(
    *,
    content: str | None = None,
    tool_calls: list[ChatCompletionMessageFunctionToolCall] | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 5,
    completion_tokens: int = 3,
) -> ChatCompletion:
    return ChatCompletion(
        id="cc_1",
        choices=[
            CompletionChoice(
                finish_reason=finish_reason,  # type: ignore[arg-type]
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", content=content, tool_calls=tool_calls
                ),
            )
        ],
        created=0,
        model="gpt-4o",
        object="chat.completion",
        usage=CompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


def test_construction_performs_no_network_call() -> None:
    client = _client()
    assert client.provider_id == "openai"
    assert client.model_id == "gpt-4o"
    assert client.capabilities.native_tool_calling is True
    assert client.capabilities.native_web_search is False


async def test_chat_normalizes_text_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    completion = _completion(content="hello")
    monkeypatch.setattr(
        client._client.chat.completions, "create", AsyncMock(return_value=completion)
    )

    response = await client.chat([Message(role=MessageRole.USER, content="hi")])

    assert response.text == "hello"
    assert response.tool_calls == ()
    assert response.stop_reason is StopReason.END_TURN
    assert response.usage.input_tokens == 5
    assert response.usage.output_tokens == 3
    assert response.usage.total_tokens == 8


async def test_chat_normalizes_tool_call_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    completion = _completion(
        tool_calls=[
            ChatCompletionMessageFunctionToolCall(
                id="call_1",
                type="function",
                function=Function(name="get_x", arguments='{"symbol": "2330"}'),
            )
        ],
        finish_reason="tool_calls",
    )
    monkeypatch.setattr(
        client._client.chat.completions, "create", AsyncMock(return_value=completion)
    )

    response = await client.chat(
        [Message(role=MessageRole.USER, content="hi")],
        tools=[ToolSpec(name="get_x", description="d", parameters={"type": "object"})],
    )

    assert response.text is None
    assert response.stop_reason is StopReason.TOOL_USE
    expected = ToolCall(id="call_1", name="get_x", arguments={"symbol": "2330"})
    assert response.tool_calls[0] == expected


async def test_chat_sends_system_and_tool_result_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    create_mock = AsyncMock(return_value=_completion(content="ok"))
    monkeypatch.setattr(client._client.chat.completions, "create", create_mock)

    await client.chat(
        [
            Message(role=MessageRole.SYSTEM, content="be caveated"),
            Message(role=MessageRole.USER, content="hi"),
            Message(
                role=MessageRole.TOOL,
                tool_results=(ToolResultPart(tool_call_id="call_1", content="42"),),
            ),
        ]
    )

    _, kwargs = create_mock.call_args
    sent = kwargs["messages"]
    assert sent[0] == {"role": "system", "content": "be caveated"}
    assert sent[2]["role"] == "tool"
    assert sent[2]["tool_call_id"] == "call_1"
    assert sent[2]["content"] == "42"


async def test_chat_never_logs_api_key_on_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = _client()
    monkeypatch.setattr(
        client._client.chat.completions,
        "create",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError):
        await client.chat([Message(role=MessageRole.USER, content="hi")])

    assert "test-key" not in caplog.text


async def test_stream_assembles_text_and_tool_call_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    chunks = [
        ChatCompletionChunk(
            id="c1",
            choices=[ChunkChoice(delta=ChoiceDelta(content="hel"), finish_reason=None, index=0)],
            created=0,
            model="gpt-4o",
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="c1",
            choices=[ChunkChoice(delta=ChoiceDelta(content="lo"), finish_reason=None, index=0)],
            created=0,
            model="gpt-4o",
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="c1",
            choices=[
                ChunkChoice(
                    delta=ChoiceDelta(
                        tool_calls=[
                            ChoiceDeltaToolCall(
                                index=0,
                                id="call_1",
                                function=ChoiceDeltaToolCallFunction(name="get_x", arguments=""),
                                type="function",
                            )
                        ]
                    ),
                    finish_reason=None,
                    index=0,
                )
            ],
            created=0,
            model="gpt-4o",
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="c1",
            choices=[
                ChunkChoice(
                    delta=ChoiceDelta(
                        tool_calls=[
                            ChoiceDeltaToolCall(
                                index=0,
                                function=ChoiceDeltaToolCallFunction(arguments='{"a":1}'),
                            )
                        ]
                    ),
                    finish_reason="tool_calls",
                    index=0,
                )
            ],
            created=0,
            model="gpt-4o",
            object="chat.completion.chunk",
            usage=CompletionUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
        ),
    ]
    monkeypatch.setattr(
        client._client.chat.completions,
        "create",
        AsyncMock(return_value=_FakeAsyncStream(chunks)),
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


async def test_count_tokens_uses_tiktoken() -> None:
    client = _client()

    count = await client.count_tokens([Message(role=MessageRole.USER, content="hello world")])

    assert count > 0
    assert isinstance(count, int)


async def test_count_tokens_never_zero_for_nonempty_input() -> None:
    client = _client()
    count = await client.count_tokens([Message(role=MessageRole.USER, content="a")])
    assert count >= 1


def test_count_tokens_falls_back_for_unknown_model() -> None:
    # gpt-5.5 isn't in tiktoken's static registry yet — construction must not raise.
    client = OpenAIClient(model_id="gpt-5.5", api_key="test-key")
    assert client.model_id == "gpt-5.5"
