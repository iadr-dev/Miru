"""A deterministic, scripted `LLMClient` stub — foundational test infra for exercising
runtime/orchestrator tests without ever hitting a real provider in the default suite.

Built now (Phase 5) even though the agent runtime/orchestrator that will consume it lands in
later phases, because it's cheap to build while already deep in the `LLMClient` Protocol and
it lets this phase's own registry/protocol-conformance tests avoid any real provider.

Not itself network-reaching or provider-specific — safe to import from any test module,
including a future `tests/agent/` or `tests/orchestrator/` package.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from llm.base import (
    LLMResponse,
    Message,
    ModelCapabilities,
    StopReason,
    StreamDoneEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    TokenUsage,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
    WebSearchResultEvent,
    WebSearchSource,
)


@dataclass
class StubLLMClient:
    """Replays a fixed script of `LLMResponse`s, one per `chat()`/`stream()` call, in order.

    Usage::

        client = StubLLMClient(script=[
            LLMResponse(text=None, tool_calls=[ToolCall(id="1", name="get_x", arguments={})],
                        stop_reason=StopReason.TOOL_USE, usage=TokenUsage(...)),
            LLMResponse(text="final answer", tool_calls=[], stop_reason=StopReason.END_TURN,
                        usage=TokenUsage(...)),
        ])

    Every call is recorded in `.calls` (the `messages`/`tools` it was given) so runtime/
    orchestrator tests can assert what was sent to the model, not just what came back.
    """

    script: Sequence[LLMResponse] = field(default_factory=lambda: ())
    provider_id: str = "stub"
    model_id: str = "stub-model"
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    # One list of thinking-delta chunk strings per scripted `stream()` call (index-aligned with
    # `script`) — a test that wants to assert `ThinkingDeltaEvent` forwarding without a live
    # provider populates this; every other test leaves it empty (no thinking events emitted).
    thinking_chunks: Sequence[Sequence[str]] = field(default_factory=lambda: ())
    # One list of text-delta chunk strings per scripted `stream()` call (index-aligned with
    # `script`), joined together must equal that call's own `response.text` — a test that wants
    # to assert `TextDeltaEvent` sentence-boundary streaming (`AgentLoop._stream_step`'s
    # `answer_delta` hook) populates this to control exactly where a sentence splits across
    # multiple deltas; every other test leaves it empty and gets the whole text in one chunk
    # (the pre-existing behavior, unaffected).
    text_chunks: Sequence[Sequence[str]] = field(default_factory=lambda: ())
    # One tuple of `WebSearchSource`s per scripted `stream()` call (index-aligned with `script`)
    # — a test that wants to assert `WebSearchResultEvent` forwarding without a live provider
    # populates this; every other test leaves it empty (no web-search event emitted).
    web_search_sources: Sequence[Sequence[WebSearchSource]] = field(default_factory=lambda: ())
    calls: list[tuple[list[Message], list[ToolSpec] | None]] = field(default_factory=lambda: [])
    # Recorded per-call so orchestrator/runtime tests can assert whether a given `chat()`/
    # `stream()` invocation requested native web search, alongside `.calls`'s messages/tools.
    native_web_search_calls: list[bool] = field(default_factory=lambda: [])
    _next_index: int = field(default=0, init=False, repr=False)

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        self.calls.append((messages, tools))
        self.native_web_search_calls.append(native_web_search)
        if self._next_index >= len(self.script):
            raise AssertionError(
                f"StubLLMClient.chat called {self._next_index + 1} times but script only has "
                f"{len(self.script)} scripted responses"
            )
        response = self.script[self._next_index]
        self._next_index += 1
        return response

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[
        TextDeltaEvent | ThinkingDeltaEvent | ToolCallEvent | WebSearchResultEvent | StreamDoneEvent
    ]:
        call_index = self._next_index
        response = await self.chat(messages, tools, native_web_search=native_web_search)
        if call_index < len(self.thinking_chunks):
            for chunk in self.thinking_chunks[call_index]:
                yield ThinkingDeltaEvent(text=chunk)
        if call_index < len(self.text_chunks) and self.text_chunks[call_index]:
            for chunk in self.text_chunks[call_index]:
                yield TextDeltaEvent(text=chunk)
        elif response.text:
            yield TextDeltaEvent(text=response.text)
        for call in response.tool_calls:
            yield ToolCallEvent(tool_call=call)
        if call_index < len(self.web_search_sources):
            sources = self.web_search_sources[call_index]
            if sources:
                yield WebSearchResultEvent(sources=tuple(sources))
        yield StreamDoneEvent(stop_reason=response.stop_reason, usage=response.usage)

    async def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") for m in messages) // 4 or 1

    def reset(self) -> None:
        """Rewind to the start of the script (for tests that reuse one stub across cases)."""
        self._next_index = 0
        self.calls.clear()


def make_text_response(text: str, *, usage: TokenUsage | None = None) -> LLMResponse:
    """Convenience builder for a plain end-turn text response."""
    return LLMResponse(
        text=text,
        tool_calls=(),
        stop_reason=StopReason.END_TURN,
        usage=usage or TokenUsage(input_tokens=10, output_tokens=10, total_tokens=20),
    )


def make_tool_call_response(
    *, tool_call_id: str, name: str, arguments: dict[str, object], usage: TokenUsage | None = None
) -> LLMResponse:
    """Convenience builder for a response that proposes exactly one tool call."""
    return LLMResponse(
        text=None,
        tool_calls=(ToolCall(id=tool_call_id, name=name, arguments=arguments),),
        stop_reason=StopReason.TOOL_USE,
        usage=usage or TokenUsage(input_tokens=10, output_tokens=10, total_tokens=20),
    )


__all__ = ["StubLLMClient", "make_text_response", "make_tool_call_response"]
