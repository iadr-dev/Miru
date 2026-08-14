"""End-to-end proof that a turn's native-web-search sources survive past the live stream: they
land as a persisted `web_search_sources` message block, not just the live `web_search.result`
SSE frame — the fix for the bug where the "N sources" pill vanished on reload because
`persisted-thread-view.tsx` had nowhere to read the sources from.

Driven through the real route, the real orchestrator, and the real event bus (same pattern as
`test_rich_card_route.py`), so this proves the whole chain: `LLMClient.stream()` ->
`WebSearchResultEvent` -> `AgentLoop.run_turn`'s `TurnResult.web_search_sources` ->
`OrchestratorResult.web_search_sources` -> `api.turn_driver._persist_answer`'s
`WebSearchSourcesBlock` -> `GET /threads/{id}` round-trip.
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
    WebSearchResultEvent,
    WebSearchSource,
)
from schemas.enums import Tier
from tests.api.conftest import build_test_client, sign_in
from tests.api.test_routes import _parse_sse

_SOURCES = (
    WebSearchSource(url="https://example.com/nvda-1", title="NVDA earnings recap"),
    WebSearchSource(url="https://example.com/nvda-2", title="NVDA stock news today"),
)


class _NativeWebSearchClient:
    """A stub `LLMClient` that claims native web-search support and streams one
    `WebSearchResultEvent` before its final answer — deterministic, no network."""

    provider_id = "stub"
    model_id = "stub-model"
    capabilities = ModelCapabilities(native_web_search=True)

    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        del messages, tools, native_web_search
        return LLMResponse(
            text=self._answer,
            tool_calls=(),
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[TextDeltaEvent | WebSearchResultEvent | StreamDoneEvent]:
        response = await self.chat(messages, tools, native_web_search=native_web_search)
        yield WebSearchResultEvent(sources=_SOURCES)
        if response.text:
            yield TextDeltaEvent(text=response.text)
        yield StreamDoneEvent(stop_reason=response.stop_reason, usage=response.usage)

    async def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") for m in messages) // 4 or 1


def _client(settings, agent_answer: str) -> TestClient:
    return build_test_client(
        settings,
        agent_answer=agent_answer,
        agent_client=_NativeWebSearchClient(agent_answer),
    )


class TestWebSearchSourcesPersist:
    def test_web_search_result_event_emits_live_sse_frame(self, settings, agent_answer) -> None:
        with _client(settings, agent_answer) as client:
            headers, _ = sign_in(client, settings, tier=Tier.FREE)
            resp = client.post(
                "/api/v1/agent/query",
                json={
                    "message": "search the web for today's Nvidia stock news",
                    "web_search": True,
                },
                headers=headers,
            )
            assert resp.status_code == 200

            frames = _parse_sse(resp.text)
            kinds = [f["event"] for f in frames]
            assert "web_search.result" in kinds, f"no web_search.result frame; saw {kinds}"

            frame = next(f for f in frames if f["event"] == "web_search.result")["data"]
            assert len(frame["sources"]) == 2

    def test_web_search_sources_are_persisted_as_a_block_and_survive_a_reload(
        self, settings, agent_answer
    ) -> None:
        """The actual acceptance criterion for the bug: after the turn ends, `GET /threads/{id}`
        (a re-opened thread's cold read — what `persisted-thread-view.tsx` renders from) still
        carries the sources, not just the now-discarded live SSE stream."""
        with _client(settings, agent_answer) as client:
            headers, _ = sign_in(client, settings, tier=Tier.FREE)
            resp = client.post(
                "/api/v1/agent/query",
                json={
                    "message": "search the web for today's Nvidia stock news",
                    "web_search": True,
                },
                headers=headers,
            )
            _parse_sse(resp.text)  # drain so the background driver finishes its persist

            thread_id = client.get("/api/v1/threads", headers=headers).json()["threads"][0]["id"]
            full = client.get(f"/api/v1/threads/{thread_id}", headers=headers).json()
            assistant = next(t for t in full["turns"] if t["role"] == "assistant")
            block = next(
                (m for m in assistant["messages"] if m["type"] == "web_search_sources"), None
            )
            assert block is not None, (
                f"no web_search_sources block persisted; saw block types "
                f"{[m['type'] for m in assistant['messages']]}"
            )
            sources = block["content"]["sources"]
            assert len(sources) == 2
            assert {s["url"] for s in sources} == {s.url for s in _SOURCES}
            assert {s["title"] for s in sources} == {s.title for s in _SOURCES}
