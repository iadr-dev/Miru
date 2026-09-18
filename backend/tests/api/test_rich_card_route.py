"""End-to-end proof that a card-opted-in tool call in a `single`-framework turn produces a real
`rich_card` SSE frame on the live `POST /agent/query` stream — driven through the real route, the
real orchestrator, and the real event bus, not a unit test of the model or the publish call.

`single` is the common one-shot path, so this is the correctness case that matters: a card
surfaced only on `subagent`/`team` fan-outs would be inert for most real turns. The test scripts a
tool-calling LLM (one step calls `get_price_quote`, the next finalizes) and a single-framework
classifier, then asserts the streamed `rich_card` frame carries the expected `card_type`/`data`
shape and the governed non-trading caveat.
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
    ToolCall,
    ToolCallEvent,
    ToolSpec,
)
from orchestrator.classifier import IntentClassifier
from orchestrator.models import Routing
from schemas.enums import Tier
from tests.api.conftest import build_test_client, sign_in
from tests.api.test_routes import _parse_sse


class _ScriptedToolThenAnswerClient:
    """A stub `LLMClient` that calls `get_price_quote` on its first turn, then returns a final
    answer once the tool result is in the transcript — deterministic, no network."""

    provider_id = "stub"
    model_id = "stub-model"
    capabilities = ModelCapabilities()

    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        del tools, native_web_search
        already_called = any(msg.tool_results for msg in messages)
        usage = TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10)
        if already_called:
            return LLMResponse(
                text=self._answer,
                tool_calls=(),
                stop_reason=StopReason.END_TURN,
                usage=usage,
            )
        return LLMResponse(
            text=None,
            tool_calls=(
                ToolCall(
                    id="call-1", name="get_price_quote", arguments={"id": "2330", "market": "TW"}
                ),
            ),
            stop_reason=StopReason.TOOL_USE,
            usage=usage,
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[TextDeltaEvent | ToolCallEvent | StreamDoneEvent]:
        response = await self.chat(messages, tools, native_web_search=native_web_search)
        if response.text:
            yield TextDeltaEvent(text=response.text)
        for call in response.tool_calls:
            yield ToolCallEvent(tool_call=call)
        yield StreamDoneEvent(stop_reason=response.stop_reason, usage=response.usage)

    async def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") for m in messages) // 4 or 1


class _SingleQuoteSkillClassifier(IntentClassifier):
    """Routes every message to `single` with the `daily-context-brief` skill, whose tool
    allowlist includes the card-opted `get_price_quote` — so the common one-shot path runs a
    card-eligible tool."""

    async def _classify_inner(self, message: str, *, history: tuple[Message, ...] = ()) -> Routing:
        del message, history
        return Routing(
            framework="single",
            skills=("daily-context-brief",),
            reason="test single-framework route to a card-opted tool",
        )


def _client(settings, agent_answer: str) -> TestClient:
    return build_test_client(
        settings,
        agent_answer=agent_answer,
        agent_client=_ScriptedToolThenAnswerClient(agent_answer),
        classifier=_SingleQuoteSkillClassifier(None),  # registry unused by the override
    )


class TestRichCardStream:
    def test_single_framework_tool_call_emits_rich_card_frame(self, settings, agent_answer) -> None:
        with _client(settings, agent_answer) as client:
            headers, _ = sign_in(client, settings, tier=Tier.FREE)
            resp = client.post(
                "/api/v1/agent/query", json={"message": "台積電報價"}, headers=headers
            )
            assert resp.status_code == 200

            frames = _parse_sse(resp.text)
            kinds = [f["event"] for f in frames]
            assert "rich_card" in kinds, f"no rich_card frame in stream; saw {kinds}"

            card = next(f for f in frames if f["event"] == "rich_card")["data"]
            assert card["card_type"] == "price_quote"
            data = card["data"]
            assert isinstance(data, dict)
            # The typed tool output's own fields survive into the card payload.
            assert data["source_id"] == "STUB"
            assert data["bar"]["close"] == 101.0
            # Governance: the structured card is stamped with the non-trading caveat, the same
            # discipline the prose answer carries.
            assert "not a buy/sell/hold recommendation" in data["non_trading_caveat"]

            # The card is ordered within the turn's live stream, between start and end.
            assert kinds[0] == "turn.start"
            assert kinds[-1] == "turn.end"

    def test_rich_card_is_persisted_as_a_block(self, settings, agent_answer) -> None:
        with _client(settings, agent_answer) as client:
            headers, _ = sign_in(client, settings, tier=Tier.FREE)
            resp = client.post(
                "/api/v1/agent/query", json={"message": "台積電報價"}, headers=headers
            )
            _parse_sse(resp.text)  # drain so the background driver finishes its persist

            thread_id = client.get("/api/v1/threads", headers=headers).json()["threads"][0]["id"]
            full = client.get(f"/api/v1/threads/{thread_id}", headers=headers).json()
            assistant = next(t for t in full["turns"] if t["role"] == "assistant")
            block_types = [m["type"] for m in assistant["messages"]]
            assert "rich_card" in block_types

    def test_unentitled_sponsor_tool_without_row_preview_yields_no_real_data_card(
        self, settings, agent_answer
    ) -> None:
        """A `sponsor` card-opted tool that has NOT opted into the governance.md §4 row-preview
        exception (`supports_row_preview` stays `False`) must never surface real underlying data
        as a card for a Free (unentitled) caller — the card is built strictly after
        `dispatch_tool`'s masking, and a whole-tool masked teaser never becomes a card. Here the
        scripted client calls `get_shareholder_dispersion` (sponsor + card-opted, no row
        preview); a Free caller must get no `rich_card` frame."""

        class _ScriptedDispersionClient(_ScriptedToolThenAnswerClient):
            async def chat(
                self,
                messages: list[Message],
                tools: list[ToolSpec] | None = None,
                *,
                native_web_search: bool = False,
            ) -> LLMResponse:
                del tools, native_web_search
                already_called = any(msg.tool_results for msg in messages)
                usage = TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10)
                if already_called:
                    return LLMResponse(
                        text=agent_answer,
                        tool_calls=(),
                        stop_reason=StopReason.END_TURN,
                        usage=usage,
                    )
                return LLMResponse(
                    text=None,
                    tool_calls=(
                        ToolCall(
                            id="call-1",
                            name="get_shareholder_dispersion",
                            arguments={"id": "2330", "market": "TW"},
                        ),
                    ),
                    stop_reason=StopReason.TOOL_USE,
                    usage=usage,
                )

        class _DispersionSkillClassifier(IntentClassifier):
            async def _classify_inner(
                self, message: str, *, history: tuple[Message, ...] = ()
            ) -> Routing:
                del message, history
                return Routing(
                    framework="single",
                    skills=("margin-and-short-interest",),
                    reason="test sponsor card masking",
                )

        client = build_test_client(
            settings,
            agent_answer=agent_answer,
            agent_client=_ScriptedDispersionClient(agent_answer),
            classifier=_DispersionSkillClassifier(None),
        )
        with client:
            headers, _ = sign_in(client, settings, tier=Tier.FREE)
            resp = client.post("/api/v1/agent/query", json={"message": "股權分散"}, headers=headers)
            frames = _parse_sse(resp.text)
            card_frames = [f for f in frames if f["event"] == "rich_card"]
            assert card_frames == [], "an unentitled sponsor call must not surface a real-data card"

    def test_unentitled_row_preview_sponsor_tool_still_yields_a_card(
        self, settings, agent_answer
    ) -> None:
        """A `sponsor` tool that HAS opted into the governance.md §4 row-preview exception
        (`supports_row_preview=True`) still runs for a Free (unentitled) caller and returns a
        real (never `MaskedTeaser`) result with its ranked rows individually locked — so this
        DOES surface as a `rich_card`, with `data.locked`/masked fields inside it, matching the
        stock-detail page's own row-masked card for the same tool. Here the scripted client calls
        `get_chip_flows`."""

        class _ScriptedChipFlowsClient(_ScriptedToolThenAnswerClient):
            async def chat(
                self,
                messages: list[Message],
                tools: list[ToolSpec] | None = None,
                *,
                native_web_search: bool = False,
            ) -> LLMResponse:
                del tools, native_web_search
                already_called = any(msg.tool_results for msg in messages)
                usage = TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10)
                if already_called:
                    return LLMResponse(
                        text=agent_answer,
                        tool_calls=(),
                        stop_reason=StopReason.END_TURN,
                        usage=usage,
                    )
                return LLMResponse(
                    text=None,
                    tool_calls=(
                        ToolCall(
                            id="call-1",
                            name="get_chip_flows",
                            arguments={"id": "2330", "market": "TW"},
                        ),
                    ),
                    stop_reason=StopReason.TOOL_USE,
                    usage=usage,
                )

        class _ChipFlowsSkillClassifier(IntentClassifier):
            async def _classify_inner(
                self, message: str, *, history: tuple[Message, ...] = ()
            ) -> Routing:
                del message, history
                return Routing(
                    framework="single",
                    skills=("chip-flow-review",),
                    reason="test row-preview card still renders",
                )

        client = build_test_client(
            settings,
            agent_answer=agent_answer,
            agent_client=_ScriptedChipFlowsClient(agent_answer),
            classifier=_ChipFlowsSkillClassifier(None),
        )
        with client:
            headers, _ = sign_in(client, settings, tier=Tier.FREE)
            resp = client.post("/api/v1/agent/query", json={"message": "分點"}, headers=headers)
            frames = _parse_sse(resp.text)
            card_frames = [f for f in frames if f["event"] == "rich_card"]
            assert len(card_frames) == 1, "a row-preview sponsor tool must still surface a card"
            assert card_frames[0]["data"]["card_type"] == "chip_flows"
            assert card_frames[0]["data"]["data"]["rows"] == []
            assert card_frames[0]["data"]["data"]["top_brokers"] == []
