"""End-to-end proof that a second turn in the same thread actually receives prior conversation
context — the fix for the bug where a follow-up like "what about its dividend history?" lost the
subject discussed in an earlier turn, because neither the classifier nor the skill's own
`AgentLoop` ever saw any turn but the current one.

Driven through the real route, the real orchestrator, and a recording stub `LLMClient` (same
pattern as `test_web_search_sources_persist.py`), so this proves the whole chain:
`api.turn_driver._load_history` -> `Orchestrator.run(history=...)` ->
`AgentLoop.run_turn(history=...)` — the second turn's model call actually contains the first
turn's user message and assistant answer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from llm.base import (
    LLMResponse,
    Message,
    ModelCapabilities,
    StopReason,
    StreamDoneEvent,
    TextDeltaEvent,
    TokenUsage,
    ToolSpec,
)
from schemas.enums import Tier
from tests.api.conftest import build_test_client, sign_in
from tests.api.test_routes import _parse_sse


class _RecordingClient:
    """A stub `LLMClient` that replays one fixed answer per call but records every call's
    `messages` — so a test can assert exactly what history (if any) reached the model, not just
    that the turn succeeded."""

    provider_id = "stub"
    model_id = "stub-model"
    capabilities = ModelCapabilities()

    def __init__(self, answers: list[str]) -> None:
        self._answers = answers
        self.calls: list[list[Message]] = []

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec] | None = None, **_: object
    ) -> LLMResponse:
        self.calls.append(list(messages))
        text = self._answers[len(self.calls) - 1]
        return LLMResponse(
            text=text,
            tool_calls=(),
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
        )

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec] | None = None, **_: object
    ) -> AsyncIterator[TextDeltaEvent | StreamDoneEvent]:
        response = await self.chat(messages, tools)
        if response.text:
            yield TextDeltaEvent(text=response.text)
        yield StreamDoneEvent(stop_reason=response.stop_reason, usage=response.usage)

    async def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") for m in messages) // 4 or 1


def _client(settings, recorder: _RecordingClient) -> TestClient:
    return build_test_client(settings, agent_answer="unused", agent_client=recorder)


def test_second_turn_in_a_thread_receives_the_first_turns_history(settings) -> None:
    recorder = _RecordingClient(
        answers=[
            "台積電 (2330) 是半導體晶圓代工廠。Informational only, not investment advice.",
            "2330 最近一次配息為每股 3.5 元。Informational only, not investment advice.",
        ]
    )
    with _client(settings, recorder) as client:
        headers, _ = sign_in(client, settings, tier=Tier.FREE)

        first = client.post(
            "/api/v1/agent/query", json={"message": "tell me about 2330"}, headers=headers
        )
        assert first.status_code == 200
        _parse_sse(first.text)

        thread_id = client.get("/api/v1/threads", headers=headers).json()["threads"][0]["id"]

        second = client.post(
            "/api/v1/agent/query",
            json={"message": "what about its dividend history?", "thread_id": thread_id},
            headers=headers,
        )
        assert second.status_code == 200
        _parse_sse(second.text)

    # The FIRST turn's model call has no prior context — nothing to carry yet.
    assert len(recorder.calls) == 2
    first_call_messages = recorder.calls[0]
    assert all("2330" not in (m.content or "") for m in first_call_messages[:-1])

    # The SECOND turn's model call must contain the first turn's user message and its own answer
    # — this is the actual regression proof: without `history` threading, the second call would
    # be exactly [system, "what about its dividend history?"], with no way to resolve "its".
    second_call_messages = recorder.calls[1]
    rendered = "\n".join(m.content or "" for m in second_call_messages)
    assert "tell me about 2330" in rendered
    assert "台積電 (2330) 是半導體晶圓代工廠" in rendered
    assert "what about its dividend history?" in second_call_messages[-1].content
