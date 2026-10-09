"""`GeminiClient` tests — the SDK is mocked (`AsyncMock` on `aio.models.generate_content`/
`.generate_content_stream`/`.count_tokens`), never a real network call in the default
profile.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from google.genai import types as genai_types

from llm.base import ImagePart, Message, MessageRole, StopReason, ToolCall, ToolResultPart, ToolSpec
from llm.gemini_client import GeminiClient, _function_call_part, _function_call_to_tool_call


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
    grounding_metadata: genai_types.GroundingMetadata | None = None,
) -> genai_types.GenerateContentResponse:
    return genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(
                content=genai_types.Content(role="model", parts=parts),
                finish_reason=finish_reason,
                grounding_metadata=grounding_metadata,
            )
        ],
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            total_token_count=prompt_tokens + output_tokens,
        ),
    )


def _thought_part(text: str) -> genai_types.Part:
    """A streamed part flagged as reasoning text — verified against the installed
    `google-genai` SDK's `Part.thought: bool | None` field, the flag distinguishing reasoning
    text from final-answer text in a streamed response."""
    part = genai_types.Part.from_text(text=text)
    part.thought = True
    return part


def test_construction_performs_no_network_call() -> None:
    client = _client()
    assert client.provider_id == "gemini"
    assert client.model_id == "gemini-3.5-flash"
    assert client.capabilities.native_web_search is True
    # gemini-3.5-flash matches `_EXTENDED_THINKING_MODEL_PREFIXES` (`gemini-3` prefix).
    assert client.capabilities.extended_thinking is True


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


async def test_chat_keeps_reasoning_text_out_of_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thought parts are not the answer: a JSON-only caller (deep research, the classifier) must
    get just the answer part, as `stream()` already does."""
    client = _client()
    response = _response(
        parts=[
            _thought_part("Okay, so the user wants JSON..."),
            genai_types.Part.from_text(text='{"a": 1}'),
        ]
    )
    monkeypatch.setattr(
        client._client.aio.models, "generate_content", AsyncMock(return_value=response)
    )

    result = await client.chat([Message(role=MessageRole.USER, content="hi")])

    assert result.text == '{"a": 1}'


async def test_chat_sends_image_part_alongside_text(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    response = _response(parts=[genai_types.Part.from_text(text="ok")])
    create_mock = AsyncMock(return_value=response)
    monkeypatch.setattr(client._client.aio.models, "generate_content", create_mock)

    image_bytes = b"fake-png-bytes"
    await client.chat(
        [
            Message(
                role=MessageRole.USER,
                content="what's in this chart?",
                images=(
                    ImagePart(
                        media_type="image/png",
                        data=base64.b64encode(image_bytes).decode("ascii"),
                    ),
                ),
            )
        ]
    )

    contents = create_mock.call_args.kwargs["contents"]
    parts = contents[0].parts
    assert parts[0].inline_data is not None
    assert parts[0].inline_data.data == image_bytes
    assert parts[0].inline_data.mime_type == "image/png"
    assert parts[1].text == "what's in this chart?"


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


async def test_chat_uses_google_search_grounding_when_native_web_search_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    generate_mock = AsyncMock(return_value=_response(parts=[genai_types.Part.from_text(text="ok")]))
    monkeypatch.setattr(client._client.aio.models, "generate_content", generate_mock)

    await client.chat(
        [Message(role=MessageRole.USER, content="hi")],
        tools=[ToolSpec(name="get_x", description="d", parameters={"type": "object"})],
        native_web_search=True,
    )

    _, kwargs = generate_mock.call_args
    tools = kwargs["config"].tools
    assert tools is not None
    assert len(tools) == 1
    assert tools[0].google_search is not None
    # Gemini rejects function-calling tools combined with google_search grounding in one
    # request — function declarations for this turn are skipped, not merged alongside it.
    assert tools[0].function_declarations is None


async def test_chat_uses_function_tools_when_native_web_search_not_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    generate_mock = AsyncMock(return_value=_response(parts=[genai_types.Part.from_text(text="ok")]))
    monkeypatch.setattr(client._client.aio.models, "generate_content", generate_mock)

    await client.chat(
        [Message(role=MessageRole.USER, content="hi")],
        tools=[ToolSpec(name="get_x", description="d", parameters={"type": "object"})],
    )

    _, kwargs = generate_mock.call_args
    tools = kwargs["config"].tools
    assert tools is not None
    assert tools[0].google_search is None
    assert tools[0].function_declarations is not None
    assert tools[0].function_declarations[0].name == "get_x"


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


async def test_stream_emits_thinking_delta_for_thought_flagged_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    chunks = [
        _response(parts=[_thought_part("pondering")], finish_reason=None),  # type: ignore[arg-type]
        _response(parts=[genai_types.Part.from_text(text="final")], finish_reason=None),  # type: ignore[arg-type]
        _response(parts=[], finish_reason=genai_types.FinishReason.STOP),
    ]
    monkeypatch.setattr(
        client._client.aio.models,
        "generate_content_stream",
        AsyncMock(return_value=_FakeAsyncIterator(chunks)),
    )

    collected = [
        event async for event in client.stream([Message(role=MessageRole.USER, content="hi")])
    ]

    thinking_events = [e for e in collected if e.type == "thinking_delta"]
    assert [e.text for e in thinking_events] == ["pondering"]  # type: ignore[union-attr]

    text_events = [e for e in collected if e.type == "text_delta"]
    assert [e.text for e in text_events] == ["final"]  # type: ignore[union-attr]


async def test_stream_emits_web_search_result_event_from_grounding_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`grounding_metadata.grounding_chunks[].web.uri`/`.title` — verified live against a real
    streamed response (see the task's live-verification pass): the whole metadata blob arrives
    on one late chunk of the stream, not incrementally."""
    client = _client()
    grounding_metadata = genai_types.GroundingMetadata(
        grounding_chunks=[
            genai_types.GroundingChunk(
                web=genai_types.GroundingChunkWeb(
                    title="investing.com",
                    uri="https://vertexaisearch.cloud.google.com/redirect/abc",
                )
            ),
            genai_types.GroundingChunk(
                web=genai_types.GroundingChunkWeb(
                    title="nvidia.com",
                    uri="https://vertexaisearch.cloud.google.com/redirect/def",
                )
            ),
        ]
    )
    chunks = [
        _response(parts=[genai_types.Part.from_text(text="answer")], finish_reason=None),  # type: ignore[arg-type]
        _response(
            parts=[],
            finish_reason=genai_types.FinishReason.STOP,
            grounding_metadata=grounding_metadata,
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

    web_search_events = [e for e in collected if e.type == "web_search_result"]
    assert len(web_search_events) == 1
    sources = web_search_events[0].sources  # type: ignore[union-attr]
    assert sources[0].title == "investing.com"
    assert sources[0].url == "https://vertexaisearch.cloud.google.com/redirect/abc"
    assert sources[1].title == "nvidia.com"


async def test_config_enables_thought_inclusion_for_extended_thinking_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    generate_mock = AsyncMock(return_value=_response(parts=[genai_types.Part.from_text(text="ok")]))
    monkeypatch.setattr(client._client.aio.models, "generate_content", generate_mock)

    await client.chat([Message(role=MessageRole.USER, content="hi")])

    _, kwargs = generate_mock.call_args
    assert kwargs["config"].thinking_config is not None
    assert kwargs["config"].thinking_config.include_thoughts is True


async def test_count_tokens_uses_sdk_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(
        client._client.aio.models,
        "count_tokens",
        AsyncMock(return_value=genai_types.CountTokensResponse(total_tokens=42)),
    )

    count = await client.count_tokens([Message(role=MessageRole.USER, content="hi")])

    assert count == 42


async def test_count_tokens_sends_no_system_instruction_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: `CountTokensConfig.system_instruction` is Vertex-only and the Developer API
    rejects it, failing every Gemini turn. The system text rides along as a leading user part."""
    client = _client()
    mock = AsyncMock(return_value=genai_types.CountTokensResponse(total_tokens=7))
    monkeypatch.setattr(client._client.aio.models, "count_tokens", mock)

    await client.count_tokens(
        [
            Message(role=MessageRole.SYSTEM, content="be brief"),
            Message(role=MessageRole.USER, content="hi"),
        ]
    )

    kwargs = mock.await_args.kwargs
    assert kwargs.get("config") is None
    assert kwargs["contents"][0].parts[0].text == "be brief"


def test_function_call_thought_signature_round_trips() -> None:
    """Regression: Gemini 3 400s ("Function call is missing a thought_signature") when a replayed
    function call loses the signature it was issued with."""
    call = _function_call_to_tool_call(
        genai_types.FunctionCall(name="propose_alert", args={"symbol": "2317"}), 0, b"sig-bytes"
    )
    assert call.continuation is not None
    assert "continuation" not in call.model_dump()  # never leaks into persisted/streamed JSON

    part = _function_call_part(call)
    assert part.thought_signature == b"sig-bytes"
    assert _function_call_part(ToolCall(id="x", name="n", arguments={})).thought_signature is None


def test_a_namespaced_function_call_name_is_reduced_to_the_registered_tool_name() -> None:
    call = _function_call_to_tool_call(
        genai_types.FunctionCall(name="google:propose_memory", args={"text": "x"}), 0
    )
    assert call.name == "propose_memory"
    plain = _function_call_to_tool_call(genai_types.FunctionCall(name="propose_alert", args={}), 0)
    assert plain.name == "propose_alert"
