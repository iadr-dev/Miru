"""Characterization of `AgentLoop._stream_step` — one Step's event consumption: sentence
buffering to `answer_delta` hooks, thinking redaction, tool-call collection, web-search
accumulation, text-recovered tool calls, the trailing flush and the stamped usage."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from agent.loop import AgentLoop
from llm.base import (
    LLMEvent,
    LLMResponse,
    Message,
    ModelCapabilities,
    StopReason,
    StreamDoneEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    TokenUsage,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEvent,
    ToolSpec,
    WebSearchResultEvent,
    WebSearchSource,
)
from tests.agent.conftest import EchoTool

_USAGE = TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3)


class _EventLLM:
    provider_id = "prov"
    model_id = "mod"
    capabilities = ModelCapabilities()

    def __init__(self, events: Sequence[LLMEvent]) -> None:
        self._events = list(events)

    async def chat(self, *args: object, **kwargs: object) -> LLMResponse:
        raise AssertionError("not used")

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[LLMEvent]:
        for event in self._events:
            yield event

    async def count_tokens(self, *args: object, **kwargs: object) -> int:
        return 0


def _text(*chunks: str) -> list[LLMEvent]:
    return [TextDeltaEvent(text=c) for c in chunks]


def _done(reason: StopReason = StopReason.END_TURN) -> StreamDoneEvent:
    return StreamDoneEvent(stop_reason=reason, usage=_USAGE)


async def _run(
    events: Sequence[LLMEvent],
) -> tuple[LLMResponse, list[WebSearchSource], list[str], list[tuple[str, object]]]:
    log: list[tuple[str, object]] = []

    async def answer(*, text: str) -> None:
        log.append(("answer", text))

    async def thinking(*, text: str) -> None:
        log.append(("thinking", text))

    async def web(*, sources: tuple[WebSearchSource, ...]) -> None:
        log.append(("web", sources))

    loop = AgentLoop(
        _EventLLM(events),  # type: ignore[arg-type]
        tools=[EchoTool()],
        max_steps=2,
        answer_delta_hooks=[answer],
        thinking_hooks=[thinking],
        web_search_hooks=[web],
    )
    sources: list[WebSearchSource] = []
    parts: list[str] = []
    response = await loop._stream_step([], web_search_sources=sources, thinking_parts=parts)
    return response, sources, parts, log


async def test_empty_stream_defaults() -> None:
    response, sources, parts, log = await _run([])
    assert response.text is None
    assert response.tool_calls == ()
    assert response.stop_reason == StopReason.END_TURN
    assert response.usage.total_tokens == 0
    assert (response.usage.provider, response.usage.model) == ("prov", "mod")
    assert (sources, parts, log) == ([], [], [])


async def test_done_event_sets_stop_reason_and_usage_and_stamps_provider() -> None:
    response, *_ = await _run([*_text("hi"), _done(StopReason.MAX_TOKENS)])
    assert response.text == "hi"
    assert response.stop_reason == StopReason.MAX_TOKENS
    assert response.usage.total_tokens == 3
    assert (response.usage.provider, response.usage.model) == ("prov", "mod")


async def test_sentences_are_forwarded_as_they_close_and_tail_is_flushed() -> None:
    response, _, _, log = await _run(_text("One. Tw", "o. Thr", "ee"))
    assert [v for k, v in log if k == "answer"] == ["One.", " Two.", " Three"]
    assert response.text == "One. Two. Three"


async def test_chunk_without_terminator_is_only_flushed_at_the_end() -> None:
    _, _, _, log = await _run(_text("no", " terminator"))
    assert log == [("answer", "no terminator")]


async def test_trading_sentence_is_redacted_per_chunk_but_response_text_is_raw() -> None:
    response, _, _, log = await _run(_text("Fine. You should buy this now. Ok."))
    joined = "".join(str(v) for k, v in log if k == "answer")
    assert "buy" not in joined.lower()
    assert "Fine." in joined
    assert response.text == "Fine. You should buy this now. Ok."


async def test_thinking_is_scanned_collected_and_forwarded_in_order() -> None:
    _, _, parts, log = await _run(
        [
            ThinkingDeltaEvent(text="ponder"),
            ThinkingDeltaEvent(text="You should buy this stock now."),
            ThinkingDeltaEvent(text=""),
        ]
    )
    assert len(parts) == 2
    assert parts[0] == "ponder"
    assert "buy" not in parts[1].lower()
    assert [k for k, _ in log] == ["thinking", "thinking"]
    assert [v for _, v in log] == parts


async def test_tool_call_events_are_collected_and_deltas_ignored() -> None:
    call = ToolCall(id="c1", name="echo_tool", arguments={"text": "x"})
    response, _, _, log = await _run(
        [
            ToolCallDeltaEvent(id="c1", name="echo_tool"),
            ToolCallEvent(tool_call=call),
            _done(StopReason.TOOL_USE),
        ]
    )
    assert response.tool_calls == (call,)
    assert response.text is None
    assert response.stop_reason == StopReason.TOOL_USE
    assert log == []


async def test_text_alongside_a_tool_call_keeps_text_and_is_not_recovered() -> None:
    call = ToolCall(id="c1", name="echo_tool", arguments={"text": "x"})
    response, _, _, log = await _run([*_text("Checking."), ToolCallEvent(tool_call=call), _done()])
    assert response.text == "Checking."
    assert response.tool_calls == (call,)
    assert log == [("answer", "Checking.")]


async def test_web_search_sources_accumulate_and_fire_hooks_in_stream_order() -> None:
    a = (WebSearchSource(url="https://example.com/a", title="A"),)
    b = (WebSearchSource(url="https://example.com/b", title="B"),)
    _, sources, _, log = await _run(
        [WebSearchResultEvent(sources=a), *_text("Done."), WebSearchResultEvent(sources=b)]
    )
    assert sources == [*a, *b]
    assert log == [("web", a), ("web", b), ("answer", "Done.")]


async def test_textual_tool_call_is_recovered_and_raw_text_dropped() -> None:
    raw = '{"name": "echo_tool", "arguments": {"text": "hello"}}'
    response, _, _, log = await _run([*_text(raw), _done()])
    assert response.text is None
    assert response.stop_reason == StopReason.TOOL_USE
    (call,) = response.tool_calls
    assert call.name == "echo_tool"
    assert call.arguments == {"text": "hello"}
    assert call.id.startswith("recovered-")
    assert log == []


async def test_unknown_json_is_not_recovered() -> None:
    raw = '{"name": "nope", "arguments": {}}'
    response, *_ = await _run(_text(raw))
    assert response.text == raw
    assert response.tool_calls == ()
    assert response.stop_reason == StopReason.END_TURN
