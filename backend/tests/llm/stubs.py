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
    TokenUsage,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
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
    calls: list[tuple[list[Message], list[ToolSpec] | None]] = field(default_factory=lambda: [])
    _next_index: int = field(default=0, init=False, repr=False)

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        self.calls.append((messages, tools))
        if self._next_index >= len(self.script):
            raise AssertionError(
                f"StubLLMClient.chat called {self._next_index + 1} times but script only has "
                f"{len(self.script)} scripted responses"
            )
        response = self.script[self._next_index]
        self._next_index += 1
        return response

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[TextDeltaEvent | ToolCallEvent | StreamDoneEvent]:
        response = await self.chat(messages, tools)
        if response.text:
            yield TextDeltaEvent(text=response.text)
        for call in response.tool_calls:
            yield ToolCallEvent(tool_call=call)
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
