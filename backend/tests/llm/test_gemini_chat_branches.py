"""Characterization of `GeminiClient.chat` response normalization: every branch of the
finish-reason / tool-call / thought-part / missing-candidate handling, SDK mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from google.genai import types as genai_types

from llm.base import LLMResponse, Message, MessageRole, StopReason
from tests.llm.test_gemini_client import _client, _response, _thought_part


async def _chat_with(
    monkeypatch: pytest.MonkeyPatch, response: genai_types.GenerateContentResponse
) -> LLMResponse:
    client = _client()
    monkeypatch.setattr(
        client._client.aio.models, "generate_content", AsyncMock(return_value=response)
    )
    return await client.chat([Message(role=MessageRole.USER, content="hi")])


@pytest.mark.parametrize(
    ("finish", "expected"),
    [
        (genai_types.FinishReason.STOP, StopReason.END_TURN),
        (genai_types.FinishReason.MAX_TOKENS, StopReason.MAX_TOKENS),
        (genai_types.FinishReason.SAFETY, StopReason.ERROR),
        (genai_types.FinishReason.RECITATION, StopReason.ERROR),
        (genai_types.FinishReason.MALFORMED_FUNCTION_CALL, StopReason.ERROR),
        (genai_types.FinishReason.OTHER, StopReason.END_TURN),  # unmapped -> END_TURN
    ],
)
async def test_chat_maps_the_finish_reason_to_a_stop_reason(
    monkeypatch: pytest.MonkeyPatch, finish: genai_types.FinishReason, expected: StopReason
) -> None:
    response = _response(parts=[genai_types.Part.from_text(text="x")], finish_reason=finish)

    result = await _chat_with(monkeypatch, response)

    assert result.stop_reason is expected


async def test_chat_tool_calls_win_over_the_finish_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response(
        parts=[genai_types.Part.from_function_call(name="get_x", args={})],
        finish_reason=genai_types.FinishReason.MAX_TOKENS,
    )

    result = await _chat_with(monkeypatch, response)

    assert result.stop_reason is StopReason.TOOL_USE


async def test_chat_without_candidates_is_an_empty_end_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = genai_types.GenerateContentResponse(candidates=None, usage_metadata=None)

    result = await _chat_with(monkeypatch, response)

    assert result.text is None
    assert result.tool_calls == ()
    assert result.stop_reason is StopReason.END_TURN
    assert result.usage.total_tokens == 0


async def test_chat_candidate_without_content_is_empty_but_keeps_its_finish_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(content=None, finish_reason=genai_types.FinishReason.SAFETY)
        ]
    )

    result = await _chat_with(monkeypatch, response)

    assert result.text is None
    assert result.stop_reason is StopReason.ERROR


async def test_chat_candidate_without_a_finish_reason_ends_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(
                content=genai_types.Content(
                    role="model", parts=[genai_types.Part.from_text(text="ok")]
                ),
                finish_reason=None,
            )
        ]
    )

    result = await _chat_with(monkeypatch, response)

    assert result.text == "ok"
    assert result.stop_reason is StopReason.END_TURN


async def test_chat_joins_text_parts_and_numbers_tool_calls_by_part_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response(
        parts=[
            _thought_part("thinking"),
            genai_types.Part.from_text(text="a"),
            genai_types.Part.from_function_call(name="ns:get_x", args={"s": 1}),
            genai_types.Part.from_text(text="b"),
            genai_types.Part(),  # neither text nor function call: ignored
            genai_types.Part.from_function_call(name="get_y", args=None),
        ]
    )

    result = await _chat_with(monkeypatch, response)

    assert result.text == "ab"
    assert [(c.id, c.name, c.arguments, c.continuation) for c in result.tool_calls] == [
        ("ns:get_x_2", "get_x", {"s": 1}, None),
        ("get_y_5", "get_y", {}, None),
    ]
    assert result.stop_reason is StopReason.TOOL_USE


async def test_chat_with_only_reasoning_text_has_no_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _chat_with(monkeypatch, _response(parts=[_thought_part("hmm")]))

    assert result.text is None
    assert result.stop_reason is StopReason.END_TURN
