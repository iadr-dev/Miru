"""Opt-in live smoke test against the real OpenAI Responses API. Never run in the default
suite — this is a hand-verified-shape rewrite of a flagship client (`openai_client.py`'s
migration from Chat Completions to Responses), which is exactly the kind of change that can be
subtly wrong in ways mocked-SDK unit tests won't catch.

Run manually with a real `OPENAI_API_KEY` configured (never logged/printed — governance §6):
    uv run pytest -m network tests/llm/test_openai_client_network.py
"""

from __future__ import annotations

import pytest

from config.settings import get_settings
from llm.base import Message, MessageRole, StopReason, ToolSpec
from llm.openai_client import OpenAIClient

pytestmark = pytest.mark.network


def _client() -> OpenAIClient:
    settings = get_settings()
    api_key = settings.openai_api_key
    assert api_key, "OPENAI_API_KEY must be set in backend/.env to run this test"
    return OpenAIClient(model_id="gpt-4o", api_key=api_key)


async def test_live_chat_returns_text() -> None:
    client = _client()

    response = await client.chat(
        [Message(role=MessageRole.USER, content="Reply with exactly the word: pong")]
    )

    assert response.text is not None
    assert response.stop_reason is StopReason.END_TURN
    assert response.usage.total_tokens > 0


async def test_live_chat_proposes_a_tool_call() -> None:
    client = _client()
    tool = ToolSpec(
        name="get_weather",
        description="Get the current weather for a city.",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )

    response = await client.chat(
        [Message(role=MessageRole.USER, content="What's the weather in Taipei right now?")],
        tools=[tool],
    )

    assert response.stop_reason is StopReason.TOOL_USE
    assert response.tool_calls
    assert response.tool_calls[0].name == "get_weather"


async def test_live_stream_yields_text_deltas_and_a_done_event() -> None:
    client = _client()

    collected = [
        event
        async for event in client.stream(
            [Message(role=MessageRole.USER, content="Count from 1 to 5.")]
        )
    ]

    assert any(event.type == "text_delta" for event in collected)
    assert collected[-1].type == "done"


async def test_live_reasoning_model_streams_thinking_deltas() -> None:
    """`gpt-5.6` (a `_REASONING_EFFORT_MODEL_PREFIXES` match) requests
    `reasoning={"effort": ..., "summary": "auto"}` — verifies the real API actually streams
    `response.reasoning_summary_text.delta` events for this request shape, and that this client
    maps them to `ThinkingDeltaEvent`, which is the entire point of this migration and cannot be
    verified against a mocked SDK. Hand-verified against the real API before this assertion was
    written: a prompt asking for an at-length explanation reliably elicits a non-empty reasoning
    summary (a terse prompt sometimes doesn't, since the model may skip summarizing trivial
    reasoning)."""
    settings = get_settings()
    api_key = settings.openai_api_key
    assert api_key, "OPENAI_API_KEY must be set in backend/.env to run this test"
    client = OpenAIClient(model_id="gpt-5.6", api_key=api_key)

    collected = [
        event
        async for event in client.stream(
            [
                Message(
                    role=MessageRole.USER,
                    content=(
                        "What is 17 * 24? Think step by step and explain your reasoning "
                        "at length before answering."
                    ),
                )
            ]
        )
    ]

    assert collected[-1].type == "done"
    assert any(event.type == "thinking_delta" for event in collected)
