"""`GeminiClient` tests — the SDK is mocked (`AsyncMock` on `aio.models.generate_content`/
`.generate_content_stream`/`.count_tokens`), never a real network call in the default
profile.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from google.genai import types as genai_types

from llm.base import Message, MessageRole, StopReason, ToolCall, ToolResultPart, ToolSpec
from llm.gemini_client import GeminiClient


class _FakeAsyncIterator:
    """A minimal stand-in for the async iterator `generate_content_stream` returns."""

    def __init__(self, chunks: list[genai_types.GenerateContentResponse]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[genai_types.GenerateContentResponse]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[genai_types.GenerateContentResponse]:
        for chunk in self._chunks:
            yield chunk


def _client() -> GeminiClient:
    return GeminiClient(model_id="gemini-3.5-flash", api_key="test-key")


def _response(
    *,
    parts: list[genai_types.Part],
    finish_reason: genai_types.FinishReason = genai_types.FinishReason.STOP,
    prompt_tokens: int = 5,
    output_tokens: int = 3,
) -> genai_types.GenerateContentResponse:
    return genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(
                content=genai_types.Content(role="model", parts=parts),
                finish_reason=finish_reason,
            )
        ],
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            total_token_count=prompt_tokens + output_tokens,
        ),
    )


def test_construction_performs_no_network_call() -> None:
    client = _client()
    assert client.provider_id == "gemini"
    assert client.model_id == "gemini-3.5-flash"
    assert client.capabilities.native_web_search is True


async def test_chat_normalizes_text_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    response = _response(parts=[genai_types.Part.from_text(text="hello")])
    monkeypatch.setattr(
        client._client.aio.models, "generate_content", AsyncMock(return_value=response)
    )

    result = await client.chat([Message(role=MessageRole.USER, content="hi")])

    assert result.text == "hello"
    assert result.tool_calls == ()
    assert result.stop_reason is StopReason.END_TURN
    assert result.usage.input_tokens == 5
    assert result.usage.output_tokens == 3
    assert result.usage.total_tokens == 8


async def test_chat_normalizes_tool_call_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    response = _response(
        parts=[
            genai_types.Part.from_function_call(name="get_x", args={"symbol": "2330"}),
        ],
        finish_reason=genai_types.FinishReason.STOP,
    )
    monkeypatch.setattr(
        client._client.aio.models, "generate_content", AsyncMock(return_value=response)
    )

    result = await client.chat(
        [Message(role=MessageRole.USER, content="hi")],
        tools=[ToolSpec(name="get_x", description="d", parameters={"type": "object"})],
    )

    assert result.text is None
    assert result.stop_reason is StopReason.TOOL_USE
    assert result.tool_calls[0].name == "get_x"
    assert result.tool_calls[0].arguments == {"symbol": "2330"}


async def test_chat_puts_system_messages_in_system_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    generate_mock = AsyncMock(return_value=_response(parts=[genai_types.Part.from_text(text="ok")]))
    monkeypatch.setattr(client._client.aio.models, "generate_content", generate_mock)

    await client.chat(
        [
            Message(role=MessageRole.SYSTEM, content="be caveated"),
            Message(role=MessageRole.USER, content="hi"),
        ]
    )

    _, kwargs = generate_mock.call_args
    assert kwargs["config"].system_instruction == "be caveated"
    assert len(kwargs["contents"]) == 1
    assert kwargs["contents"][0].role == "user"


async def test_chat_recovers_function_name_for_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    generate_mock = AsyncMock(return_value=_response(parts=[genai_types.Part.from_text(text="ok")]))
    monkeypatch.setattr(client._client.aio.models, "generate_content", generate_mock)

    await client.chat(
        [
            Message(role=MessageRole.USER, content="what's the weather"),
            Message(
                role=MessageRole.ASSISTANT,
                tool_calls=(ToolCall(id="call_1", name="get_weather", arguments={}),),
            ),
            Message(
                role=MessageRole.TOOL,
                tool_results=(ToolResultPart(tool_call_id="call_1", content="sunny"),),
            ),
        ]
    )

    _, kwargs = generate_mock.call_args
    tool_result_content = kwargs["contents"][2]
    assert tool_result_content.role == "user"
    assert tool_result_content.parts[0].function_response.name == "get_weather"
    assert tool_result_content.parts[0].function_response.response["content"] == "sunny"


async def test_chat_never_logs_api_key_on_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = _client()
    monkeypatch.setattr(
        client._client.aio.models,
        "generate_content",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError):
        await client.chat([Message(role=MessageRole.USER, content="hi")])

    assert "test-key" not in caplog.text


async def test_stream_assembles_text_and_tool_call_events(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    chunks = [
        _response(parts=[genai_types.Part.from_text(text="hel")], finish_reason=None),  # type: ignore[arg-type]
        _response(parts=[genai_types.Part.from_text(text="lo")], finish_reason=None),  # type: ignore[arg-type]
        _response(
            parts=[genai_types.Part.from_function_call(name="get_x", args={"a": 1})],
            finish_reason=genai_types.FinishReason.STOP,
        ),
    ]
    monkeypatch.setattr(
        client._client.aio.models,
        "generate_content_stream",
        AsyncMock(return_value=_FakeAsyncIterator(chunks)),
    )

    collected = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    text_events = [e for e in collected if e.type == "text_delta"]
    assert "".join(e.text for e in text_events) == "hello"  # type: ignore[union-attr]

    tool_call_events = [e for e in collected if e.type == "tool_call"]
    assert tool_call_events[0].tool_call.name == "get_x"  # type: ignore[union-attr]
    assert tool_call_events[0].tool_call.arguments == {"a": 1}  # type: ignore[union-attr]

    done = collected[-1]
    assert done.type == "done"
    assert done.stop_reason is StopReason.TOOL_USE  # type: ignore[union-attr]
    assert done.usage.input_tokens == 5  # type: ignore[union-attr]
    assert done.usage.output_tokens == 3  # type: ignore[union-attr]


async def test_count_tokens_uses_sdk_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(
        client._client.aio.models,
        "count_tokens",
        AsyncMock(return_value=genai_types.CountTokensResponse(total_tokens=42)),
    )

    count = await client.count_tokens([Message(role=MessageRole.USER, content="hi")])

    assert count == 42
