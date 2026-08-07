"""The `LLMClient` Protocol + the normalized wire format.

Every provider client converts to/from these frozen Pydantic v2 models so the agent runtime
(Phase 6, not yet built) sees **one** message/tool-call/response shape regardless of vendor.
This module has zero dependency on `tools/`, `context/`, `dataaccess/`, or
`sources/` — `llm/` is a leaf-ish module used by the (future) agent runtime alongside
`tools/`, never the other way around, per the project's one-way dependency rule.

Design notes (judgment calls):
- One natural sketch is a single `chat(..., stream: bool = False) ->
  LLMResponse | Iterator[LLMEvent]` method. We split this into two Protocol methods —
  `chat()` (always a single `LLMResponse`) and `stream()` (always an `AsyncIterator[LLMEvent]`)
  — because a `bool`-discriminated union return defeats pyright/mypy strict at every call site
  and this project is async end-to-end, not `Iterator`.
- `Message` models the **normalized turn shape**: a role plus optional text, optional
  assistant-proposed `tool_calls`, and optional `tool_results` (for `role="tool"` messages
  reporting a prior tool call's outcome back to the model). This is deliberately generic
  enough to round-trip through Anthropic/OpenAI-shaped/Gemini-shaped wire formats.
- `ToolSpec` is the LLM-facing tool schema (name/description/JSON-schema-for-args) — **not**
  the `Tool` protocol from `tools/` (Phase 4, not yet built). `llm/` never imports `tools/`.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class MessageRole(StrEnum):
    """Normalized message role — mapped to each provider's own role vocabulary at the edge."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class StopReason(StrEnum):
    """Normalized stop reason — mapped from each provider's own vocabulary at the edge."""

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    ERROR = "error"


class ToolCall(BaseModel):
    """One normalized tool-call proposal from the model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    arguments: dict[str, object]


class ToolResultPart(BaseModel):
    """The outcome of a prior tool call, reported back to the model in a `role=tool` message.

    `content` is a string (already-rendered tool output) — the LLM adapter does not know or
    care about the tool's own typed output model; that normalization happens in `tools/`
    (Phase 4), strictly below this layer's concern.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_call_id: str
    content: str
    is_error: bool = False


class Message(BaseModel):
    """One normalized turn in the conversation sent to `chat`/`stream`.

    Exactly one of `content`/`tool_calls`/`tool_results` is expected to be populated per the
    message's `role`, but this is a wire-format convenience type, not itself a governance
    boundary — validation of *content* (caveats, non-trading) is `agent/guardrails.py`'s job
    (Phase 6), not the adapter's.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: MessageRole
    content: str | None = None
    # tuple, not list — frozen model, and avoids a pyright `list[Unknown]` inference edge on
    # Pydantic v2 `Field(default_factory=list)` (see `context/canonical.py` for precedent).
    tool_calls: tuple[ToolCall, ...] = Field(default_factory=tuple)
    tool_results: tuple[ToolResultPart, ...] = Field(default_factory=tuple)


class ToolSpec(BaseModel):
    """The LLM-facing tool schema — name/description/JSON-schema-for-args.

    This is intentionally decoupled from `tools.base.Tool` (Phase 4): the adapter only needs
    enough to build a provider's native tool-calling payload.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    parameters: dict[str, object]  # JSON Schema object, e.g. {"type": "object", ...}


class TokenUsage(BaseModel):
    """Normalized token accounting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class LLMResponse(BaseModel):
    """The normalized non-streaming response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str | None
    tool_calls: tuple[ToolCall, ...] = Field(default_factory=tuple)
    stop_reason: StopReason
    usage: TokenUsage


class ModelCapabilities(BaseModel):
    """Per-model capability flags.

    Lets the adapter degrade gracefully — e.g. prompt-based tool-call emulation where a model
    lacks native tool-calling — instead of assuming every model can do everything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    native_tool_calling: bool = True
    streaming: bool = True
    extended_thinking: bool = False
    native_web_search: bool = False


# ---- Streaming event types (typed chunks) ----


class TextDeltaEvent(BaseModel):
    """An incremental text chunk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolCallDeltaEvent(BaseModel):
    """An incremental fragment of a tool call being assembled (name and/or a JSON-arguments
    fragment — providers stream tool-call arguments as partial JSON text)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["tool_call_delta"] = "tool_call_delta"
    id: str
    name: str | None = None
    arguments_delta: str | None = None


class ToolCallEvent(BaseModel):
    """A fully-assembled tool call, emitted once its arguments JSON is complete."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["tool_call"] = "tool_call"
    tool_call: ToolCall


class StreamDoneEvent(BaseModel):
    """Terminal event — carries the same stop reason + usage a non-streaming call would."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["done"] = "done"
    stop_reason: StopReason
    usage: TokenUsage


LLMEvent = TextDeltaEvent | ToolCallDeltaEvent | ToolCallEvent | StreamDoneEvent


@runtime_checkable
class LLMClient(Protocol):
    """Provider-agnostic chat interface.

    Depend on this Protocol everywhere outside `llm/registry.py` and the provider-client
    files themselves — never a concrete provider class.

    Constructing a client must **never** perform network I/O (no import-time / construction-
    time network calls) — only `chat()`/`stream()`/`count_tokens()` do.
    """

    provider_id: str
    model_id: str
    capabilities: ModelCapabilities

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
    ) -> LLMResponse:
        """One non-streaming turn. Never raises on a provider error surfaced as content —
        transport/auth errors are the caller's concern to catch and handle; retry/fallback-chain
        policy is left to the future agent runtime, not this seam."""
        ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """One streaming turn — an async generator of typed chunks, terminated by exactly one
        `StreamDoneEvent`."""
        ...

    @abstractmethod
    async def count_tokens(self, messages: list[Message]) -> int:
        """Count (or, where the provider has no counting endpoint, estimate) the token cost
        of `messages` — used by the agent runtime's context-window budgeting (Phase 6)."""
        ...


__all__ = [
    "LLMClient",
    "LLMEvent",
    "LLMResponse",
    "Message",
    "MessageRole",
    "ModelCapabilities",
    "StopReason",
    "StreamDoneEvent",
    "TextDeltaEvent",
    "TokenUsage",
    "ToolCall",
    "ToolCallDeltaEvent",
    "ToolCallEvent",
    "ToolResultPart",
    "ToolSpec",
]
