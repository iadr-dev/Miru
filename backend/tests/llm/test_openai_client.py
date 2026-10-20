"""`OpenAIClient` tests — the SDK is mocked (`AsyncMock` on `responses.create`), never a real
network call in the default profile. `OpenAIClient` talks to OpenAI's Responses API (not Chat
Completions) end-to-end.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionToolCall,
    ResponseFunctionToolCallParam,
    ResponseOutputItemAddedEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseTextDeltaEvent,
    ResponseUsage,
)
from openai.types.responses.response import IncompleteDetails
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from llm.base import ImagePart, Message, MessageRole, StopReason, ToolCall, ToolResultPart, ToolSpec
from llm.openai_client import OpenAIClient


class _FakeAsyncStream:
    """A minimal stand-in for `openai`'s `AsyncStream[ResponseStreamEvent]` — enough surface
    for `OpenAIClient.stream()` (`async for event in response`), without depending on the SDK's
    real network-backed implementation."""

    def __init__(self, events: list[object]) -> None:
        self._events = events

    def __aiter__(self) -> AsyncIterator[object]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[object]:
        for event in self._events:
            yield event


def _client() -> OpenAIClient:
    return OpenAIClient(model_id="gpt-4o", api_key="test-key")


def _usage(input_tokens: int = 5, output_tokens: int = 3) -> ResponseUsage:
    return ResponseUsage(
        input_tokens=input_tokens,
        input_tokens_details=InputTokensDetails(cached_tokens=0, cache_write_tokens=0),
        output_tokens=output_tokens,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        total_tokens=input_tokens + output_tokens,
    )


def _response(
    *,
    text: str | None = None,
    tool_calls: list[ResponseFunctionToolCall] | None = None,
    status: str = "completed",
    incomplete_reason: str | None = None,
    usage: ResponseUsage | None = None,
) -> Response:
    output: list[object] = []
    if text is not None:
        output.append(
            ResponseOutputMessage(
                id="msg_1",
                content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
                role="assistant",
                status="completed",
                type="message",
            )
        )
    if tool_calls:
        output.extend(tool_calls)
    incomplete_details = None
    if incomplete_reason is not None:
        incomplete_details = IncompleteDetails(reason=incomplete_reason)  # type: ignore[arg-type]
    return Response(
        id="resp_1",
        created_at=0,
        error=None,
        incomplete_details=incomplete_details,
        instructions=None,
        metadata=None,
        model="gpt-4o",
        object="response",
        output=output,  # type: ignore[arg-type]
        parallel_tool_calls=True,
        temperature=None,
        tool_choice="auto",
        tools=[],
        top_p=None,
        status=status,  # type: ignore[arg-type]
        usage=usage or _usage(),
    )


def test_construction_performs_no_network_call() -> None:
    client = _client()
    assert client.provider_id == "openai"
    assert client.model_id == "gpt-4o"
    assert client.capabilities.native_tool_calling is True
    assert client.capabilities.native_web_search is False


async def test_chat_normalizes_text_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    response = _response(text="hello")
    monkeypatch.setattr(client._client.responses, "create", AsyncMock(return_value=response))

    result = await client.chat([Message(role=MessageRole.USER, content="hi")])

    assert result.text == "hello"
    assert result.tool_calls == ()
    assert result.stop_reason is StopReason.END_TURN
    assert result.usage.input_tokens == 5
    assert result.usage.output_tokens == 3
    assert result.usage.total_tokens == 8


async def test_chat_sends_input_image_alongside_text(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    response = _response(text="ok")
    create_mock = AsyncMock(return_value=response)
    monkeypatch.setattr(client._client.responses, "create", create_mock)

    await client.chat(
        [
            Message(
                role=MessageRole.USER,
                content="what's in this chart?",
                images=(ImagePart(media_type="image/png", data="Zm9v"),),
            )
        ]
    )

    input_items = create_mock.call_args.kwargs["input"]
    message_item = input_items[0]
    content = message_item["content"]
    assert content[0]["type"] == "input_image"
    assert content[0]["image_url"] == "data:image/png;base64,Zm9v"
    assert content[1] == {"type": "input_text", "text": "what's in this chart?"}


async def test_chat_normalizes_tool_call_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    response = _response(
        tool_calls=[
            ResponseFunctionToolCall(
                call_id="call_1",
                name="get_x",
                arguments='{"symbol": "2330"}',
                type="function_call",
            )
        ],
        status="completed",
    )
    monkeypatch.setattr(client._client.responses, "create", AsyncMock(return_value=response))

    result = await client.chat(
        [Message(role=MessageRole.USER, content="hi")],
        tools=[ToolSpec(name="get_x", description="d", parameters={"type": "object"})],
    )

    assert result.text is None
    assert result.stop_reason is StopReason.TOOL_USE
    expected = ToolCall(id="call_1", name="get_x", arguments={"symbol": "2330"})
    assert result.tool_calls[0] == expected


async def test_chat_sends_input_items_for_system_user_and_tool_result_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    create_mock = AsyncMock(return_value=_response(text="ok"))
    monkeypatch.setattr(client._client.responses, "create", create_mock)

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
    sent = kwargs["input"]
    assert sent[0]["role"] == "developer"
    assert sent[0]["content"] == [{"type": "input_text", "text": "be caveated"}]
    assert sent[1]["role"] == "user"
    assert sent[1]["content"] == [{"type": "input_text", "text": "hi"}]
    assert sent[2]["type"] == "function_call_output"
    assert sent[2]["call_id"] == "call_1"
    assert sent[2]["output"] == "42"


async def test_chat_round_trips_a_prior_assistant_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model-proposed tool call, replayed back as history (assistant `tool_calls` + the
    matching `role=tool` result) must become a `function_call` item followed by its
    `function_call_output` item — verifies the multi-turn round-trip shape."""
    client = _client()
    create_mock = AsyncMock(return_value=_response(text="done"))
    monkeypatch.setattr(client._client.responses, "create", create_mock)

    await client.chat(
        [
            Message(role=MessageRole.USER, content="what is 2330?"),
            Message(
                role=MessageRole.ASSISTANT,
                tool_calls=(ToolCall(id="call_1", name="get_x", arguments={"symbol": "2330"}),),
            ),
            Message(
                role=MessageRole.TOOL,
                tool_results=(ToolResultPart(tool_call_id="call_1", content="42"),),
            ),
        ]
    )

    _, kwargs = create_mock.call_args
    sent = kwargs["input"]
    assert sent[1] == ResponseFunctionToolCallParam(
        type="function_call",
        call_id="call_1",
        name="get_x",
        arguments='{"symbol": "2330"}',
    )
    assert sent[2]["type"] == "function_call_output"
    assert sent[2]["call_id"] == "call_1"


async def test_chat_maps_incomplete_max_output_tokens_to_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    response = _response(text="cut off", status="incomplete", incomplete_reason="max_output_tokens")
    monkeypatch.setattr(client._client.responses, "create", AsyncMock(return_value=response))

    result = await client.chat([Message(role=MessageRole.USER, content="hi")])

    assert result.stop_reason is StopReason.MAX_TOKENS


async def test_chat_maps_failed_status_to_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    response = _response(status="failed")
    monkeypatch.setattr(client._client.responses, "create", AsyncMock(return_value=response))

    result = await client.chat([Message(role=MessageRole.USER, content="hi")])

    assert result.stop_reason is StopReason.ERROR


async def test_chat_sets_reasoning_effort_for_a_supported_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = OpenAIClient(model_id="gpt-5.6", api_key="test-key")
    create_mock = AsyncMock(return_value=_response(text="ok"))
    monkeypatch.setattr(client._client.responses, "create", create_mock)

    await client.chat([Message(role=MessageRole.USER, content="hi")])

    _, kwargs = create_mock.call_args
    assert kwargs["reasoning"]["effort"] == "medium"
    assert kwargs["reasoning"]["summary"] == "auto"


async def test_chat_omits_reasoning_for_an_unsupported_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()  # gpt-4o — not in the reasoning-effort model prefixes
    create_mock = AsyncMock(return_value=_response(text="ok"))
    monkeypatch.setattr(client._client.responses, "create", create_mock)

    await client.chat([Message(role=MessageRole.USER, content="hi")])

    _, kwargs = create_mock.call_args
    assert "reasoning" not in kwargs


async def test_reasoning_effort_constructor_override_wins_over_model_heuristic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = OpenAIClient(model_id="gpt-4o", api_key="test-key", reasoning_effort="high")
    create_mock = AsyncMock(return_value=_response(text="ok"))
    monkeypatch.setattr(client._client.responses, "create", create_mock)

    await client.chat([Message(role=MessageRole.USER, content="hi")])

    _, kwargs = create_mock.call_args
    assert kwargs["reasoning"]["effort"] == "high"


async def test_chat_never_logs_api_key_on_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = _client()
    monkeypatch.setattr(
        client._client.responses,
        "create",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    messages = [Message(role=MessageRole.USER, content="hi")]
    with pytest.raises(RuntimeError):
        await client.chat(messages)

    assert "test-key" not in caplog.text


async def test_stream_assembles_text_thinking_and_tool_call_deltas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    final_response = _response(
        tool_calls=[
            ResponseFunctionToolCall(
                call_id="call_1", name="get_x", arguments='{"a":1}', type="function_call"
            )
        ],
        usage=_usage(),
    )
    events = [
        ResponseTextDeltaEvent(
            content_index=0,
            delta="hel",
            item_id="msg_1",
            logprobs=[],
            output_index=0,
            sequence_number=1,
            type="response.output_text.delta",
        ),
        ResponseTextDeltaEvent(
            content_index=0,
            delta="lo",
            item_id="msg_1",
            logprobs=[],
            output_index=0,
            sequence_number=2,
            type="response.output_text.delta",
        ),
        ResponseReasoningSummaryTextDeltaEvent(
            delta="thinking...",
            item_id="reasoning_1",
            output_index=1,
            sequence_number=3,
            summary_index=0,
            type="response.reasoning_summary_text.delta",
        ),
        ResponseOutputItemAddedEvent(
            item=ResponseFunctionToolCall(
                call_id="call_1", name="get_x", arguments="", type="function_call"
            ),
            output_index=2,
            sequence_number=4,
            type="response.output_item.added",
        ),
        ResponseFunctionCallArgumentsDeltaEvent(
            delta='{"a":1}',
            item_id="fc_1",
            output_index=2,
            sequence_number=5,
            type="response.function_call_arguments.delta",
        ),
        ResponseCompletedEvent(
            response=final_response, sequence_number=6, type="response.completed"
        ),
    ]
    monkeypatch.setattr(
        client._client.responses,
        "create",
        AsyncMock(return_value=_FakeAsyncStream(events)),
    )

    collected = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    text_events = [e for e in collected if e.type == "text_delta"]
    assert "".join(e.text for e in text_events) == "hello"  # type: ignore[union-attr]

    thinking_events = [e for e in collected if e.type == "thinking_delta"]
    assert thinking_events[0].text == "thinking..."  # type: ignore[union-attr]

    tool_call_events = [e for e in collected if e.type == "tool_call"]
    assert tool_call_events[0].tool_call == ToolCall(id="call_1", name="get_x", arguments={"a": 1})  # type: ignore[union-attr]

    done = collected[-1]
    assert done.type == "done"
    assert done.stop_reason is StopReason.TOOL_USE  # type: ignore[union-attr]
    assert done.usage.input_tokens == 5  # type: ignore[union-attr]
    assert done.usage.output_tokens == 3  # type: ignore[union-attr]


async def test_stream_terminal_event_without_tool_calls_ends_at_end_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    events = [
        ResponseTextDeltaEvent(
            content_index=0,
            delta="hi",
            item_id="msg_1",
            logprobs=[],
            output_index=0,
            sequence_number=1,
            type="response.output_text.delta",
        ),
        ResponseCompletedEvent(
            response=_response(text="hi"), sequence_number=2, type="response.completed"
        ),
    ]
    monkeypatch.setattr(
        client._client.responses,
        "create",
        AsyncMock(return_value=_FakeAsyncStream(events)),
    )

    collected = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    done = collected[-1]
    assert done.type == "done"
    assert done.stop_reason is StopReason.END_TURN  # type: ignore[union-attr]


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


async def test_count_tokens_tokenizes_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    seen: list[int] = []
    original = client._count_tokens_sync

    def spy(messages: list[Message]) -> int:
        seen.append(threading.get_ident())
        return original(messages)

    monkeypatch.setattr(client, "_count_tokens_sync", spy)

    await client.count_tokens([Message(role=MessageRole.USER, content="hello world")])

    assert seen
    assert threading.get_ident() not in seen
