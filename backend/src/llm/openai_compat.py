"""Shared base for the three OpenAI-chat-completions-compatible providers:
Groq, Cerebras, OpenRouter. One parameterized client (`base_url` + `model` + API key) rather
than three thin subclasses — the matrix is cheap to extend because the wire
format is identical; only `base_url`/env-key-name/default-model differ, which is data, not
behavior. `llm/registry.py` constructs one `OpenAICompatClient` per provider id with the
right `base_url`.

Fully implemented + tested (unlike `openai_client.py`/`gemini_client.py`, see report):
non-streaming `chat`, streaming `stream` (SSE `data:` framing), and a heuristic
`count_tokens` (no token-counting endpoint on these APIs; ~4 chars/token estimate, clearly
documented as an estimate, since these providers offer no exact token count to use instead).

No network call happens at construction — only `chat()`/`stream()`/`count_tokens()` (the
latter doesn't call the network at all, it's a local estimate).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Final, cast

import httpx
import structlog

from llm.base import (
    LLMResponse,
    Message,
    MessageRole,
    ModelCapabilities,
    StopReason,
    StreamDoneEvent,
    TextDeltaEvent,
    TokenUsage,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEvent,
    ToolSpec,
)

logger = structlog.get_logger(__name__)

_CHARS_PER_TOKEN_ESTIMATE: Final[float] = 4.0  # documented estimate — no real token-counting
# endpoint exists for these providers
_FINISH_REASON_MAP: Final[dict[str, StopReason]] = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
}

# The untyped JSON-object edge every `cast(...)` below narrows an httpx-decoded response value
# from — one PEP-695 local alias instead of repeating the same cast-target string at every call
# site (this module's wire responses are dynamically-shaped JSON, not a typed SDK response).
type _JsonObject = dict[str, object]


def _message_to_wire(message: Message) -> dict[str, object]:
    if message.role is MessageRole.TOOL:
        # OpenAI-compat represents each tool result as its own `role: tool` message.
        # Callers are expected to pass exactly one ToolResultPart per Message of this role;
        # multiple results become multiple Message entries upstream (llm/base.py docstring).
        part = message.tool_results[0]
        return {"role": "tool", "tool_call_id": part.tool_call_id, "content": part.content}
    wire: dict[str, object] = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    return wire


def _tool_spec_to_wire(spec: ToolSpec) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def _usage_from_raw(usage_raw: dict[str, object]) -> TokenUsage:
    return TokenUsage(
        input_tokens=int(cast("int", usage_raw.get("prompt_tokens", 0)) or 0),
        output_tokens=int(cast("int", usage_raw.get("completion_tokens", 0)) or 0),
        total_tokens=int(cast("int", usage_raw.get("total_tokens", 0)) or 0),
    )


def _apply_tool_call_delta(
    item: dict[str, object], pending_calls: dict[int, dict[str, str]]
) -> ToolCallDeltaEvent:
    """Fold one raw `tool_calls[i]` delta object into `pending_calls` and return the
    normalized delta event (helper split out of `stream()` to keep its branch/statement
    count under the ruff PLR0912/PLR0915 limits)."""
    index = int(cast("int", item.get("index", 0)) or 0)
    slot = pending_calls.setdefault(index, {"id": "", "name": "", "args": ""})
    call_id = item.get("id")
    if isinstance(call_id, str) and call_id:
        slot["id"] = call_id
    name_delta: str | None = None
    args_delta: str | None = None
    fn = item.get("function")
    if isinstance(fn, dict):
        fn_dict = cast(_JsonObject, fn)
        name = fn_dict.get("name")
        if isinstance(name, str) and name:
            slot["name"] = name
            name_delta = name
        args = fn_dict.get("arguments")
        if isinstance(args, str) and args:
            slot["args"] += args
            args_delta = args
    return ToolCallDeltaEvent(id=slot["id"], name=name_delta, arguments_delta=args_delta)


def _sse_data(line: str) -> str | None:
    if not line.startswith("data:"):
        return None
    return line.removeprefix("data:").strip()


def _first_choice(chunk: dict[str, object]) -> dict[str, object] | None:
    choices = cast("list[dict[str, object]]", chunk.get("choices", []))
    return choices[0] if choices else None


def _events_from_delta(
    choice: dict[str, object], pending_calls: dict[int, dict[str, str]]
) -> list[TextDeltaEvent | ToolCallDeltaEvent]:
    """Normalize one choice's `delta` object into zero or more typed events (helper split out
    of `stream()` to keep its branch/statement count under the ruff PLR0912/PLR0915 limits)."""
    events: list[TextDeltaEvent | ToolCallDeltaEvent] = []
    delta = cast(_JsonObject, choice.get("delta", {}))
    text = delta.get("content")
    if isinstance(text, str) and text:
        events.append(TextDeltaEvent(text=text))
    raw_tool_deltas = delta.get("tool_calls")
    if isinstance(raw_tool_deltas, list):
        for raw_delta in cast("list[object]", raw_tool_deltas):
            if isinstance(raw_delta, dict):
                events.append(_apply_tool_call_delta(cast(_JsonObject, raw_delta), pending_calls))
    return events


def _parse_json_object(raw: str) -> dict[str, object]:
    """Parse a (possibly empty/malformed) JSON-arguments string into a typed
    `dict[str, object]`, never raising and never letting an `Any` escape this module."""
    if not raw:
        return {}
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return cast(_JsonObject, parsed) if isinstance(parsed, dict) else {}


def _parse_tool_calls(raw_calls: object) -> list[ToolCall]:
    if not isinstance(raw_calls, list):
        return []
    calls: list[ToolCall] = []
    for raw in cast("list[object]", raw_calls):
        if not isinstance(raw, dict):
            continue
        item = cast(_JsonObject, raw)
        fn = item.get("function")
        if not isinstance(fn, dict):
            continue
        fn_dict = cast(_JsonObject, fn)
        arguments_raw = fn_dict.get("arguments")
        arguments = _parse_json_object(arguments_raw) if isinstance(arguments_raw, str) else {}
        calls.append(
            ToolCall(
                id=str(item.get("id", "")), name=str(fn_dict.get("name", "")), arguments=arguments
            )
        )
    return calls


_SSE_DONE = "[DONE]"


@dataclass
class _SseLineResult:
    """One SSE `data:` line's parsed outcome — pulled out of `stream()` purely to keep that
    method's own branching shallow. `done=True` means the line was the terminal `[DONE]`
    sentinel; `events`/`stop_reason`/`usage` are empty/`None` when the line carried nothing
    new (a blank keep-alive line, or a chunk with no usable choice)."""

    done: bool = False
    events: list[TextDeltaEvent | ToolCallDeltaEvent] = field(
        default_factory=list[TextDeltaEvent | ToolCallDeltaEvent]
    )
    stop_reason: StopReason | None = None
    usage: TokenUsage | None = None


def _parse_sse_line(line: str, pending_calls: dict[int, dict[str, str]]) -> _SseLineResult:
    data = _sse_data(line)
    if data is None:
        return _SseLineResult()
    if data == _SSE_DONE:
        return _SseLineResult(done=True)
    chunk = cast(_JsonObject, json.loads(data))
    usage = (
        _usage_from_raw(cast(_JsonObject, chunk["usage"]))
        if isinstance(chunk.get("usage"), dict)
        else None
    )
    choice = _first_choice(chunk)
    if choice is None:
        return _SseLineResult(usage=usage)
    finish_reason = choice.get("finish_reason")
    stop_reason = (
        _FINISH_REASON_MAP.get(finish_reason, StopReason.END_TURN)
        if isinstance(finish_reason, str)
        else None
    )
    return _SseLineResult(
        events=_events_from_delta(choice, pending_calls), stop_reason=stop_reason, usage=usage
    )


class OpenAICompatClient:
    """One `LLMClient` for any OpenAI-chat-completions-compatible provider.

    `provider_id` is the config-facing id (`groq`/`cerebras`/`openrouter`); `base_url` and
    the API key are supplied by `llm/registry.py` from `Settings` — never hardcoded here.
    """

    def __init__(  # noqa: PLR0913 — six keyword-only ctor params (provider/model/base_url/key
        # + two rarely-overridden knobs); splitting into a config object would obscure, not
        # clarify, the one place `llm/registry.py` wires provider-specific config from Settings.
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        api_key: str,
        capabilities: ModelCapabilities | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.provider_id = provider_id
        self.model_id = model_id
        self.capabilities = capabilities or ModelCapabilities()
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key  # never logged (governance §6) — held only in this instance
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "content-type": "application/json"}

    def _body(
        self, messages: list[Message], tools: list[ToolSpec] | None, *, stream: bool
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self.model_id,
            "messages": [_message_to_wire(m) for m in messages],
            "stream": stream,
        }
        if tools:
            body["tools"] = [_tool_spec_to_wire(t) for t in tools]
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        body = self._body(messages, tools, stream=False)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    f"{self._base_url}/chat/completions", headers=self._headers(), json=body
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # Never surface the Authorization header / key in the log (governance §6).
                logger.warning(
                    "llm_openai_compat_http_error",
                    provider=self.provider_id,
                    status_code=exc.response.status_code,
                )
                raise
            raw: dict[str, object] = response.json()

        choice = cast("list[dict[str, object]]", raw["choices"])[0]
        message = cast(_JsonObject, choice["message"])
        content = message.get("content")
        tool_calls = _parse_tool_calls(message.get("tool_calls"))
        finish_reason = str(choice.get("finish_reason", "stop"))
        usage_raw = cast(_JsonObject, raw.get("usage", {}))
        return LLMResponse(
            text=content if isinstance(content, str) else None,
            tool_calls=tuple(tool_calls),
            stop_reason=_FINISH_REASON_MAP.get(finish_reason, StopReason.END_TURN),
            usage=_usage_from_raw(usage_raw),
        )

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[TextDeltaEvent | ToolCallDeltaEvent | ToolCallEvent | StreamDoneEvent]:
        body = self._body(messages, tools, stream=True)
        # Assembles partial tool-call argument fragments keyed by index (OpenAI-compat streams
        # tool calls by array index, filling in id/name once then appending arguments deltas).
        pending_calls: dict[int, dict[str, str]] = {}
        stop_reason = StopReason.END_TURN
        usage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)

        async with (
            httpx.AsyncClient(timeout=self._timeout) as client,
            client.stream(
                "POST", f"{self._base_url}/chat/completions", headers=self._headers(), json=body
            ) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                result = _parse_sse_line(line, pending_calls)
                if result.done:
                    break
                if result.usage is not None:
                    usage = result.usage
                if result.stop_reason is not None:
                    stop_reason = result.stop_reason
                for event in result.events:
                    yield event

        for slot in pending_calls.values():
            yield ToolCallEvent(
                tool_call=ToolCall(
                    id=slot["id"], name=slot["name"], arguments=_parse_json_object(slot["args"])
                )
            )
        yield StreamDoneEvent(stop_reason=stop_reason, usage=usage)

    async def count_tokens(self, messages: list[Message]) -> int:
        """Heuristic estimate (no counting endpoint on Groq/Cerebras/OpenRouter APIs):
        ~4 characters per token, summed over all message text content. Adequate for the
        context-window budgeting consumer (Phase 6) which needs an order-of-magnitude
        figure, not an exact count."""
        total_chars = sum(len(m.content or "") for m in messages)
        for m in messages:
            for call in m.tool_calls:
                total_chars += len(call.name) + len(json.dumps(call.arguments))
            for result in m.tool_results:
                total_chars += len(result.content)
        return max(1, int(total_chars / _CHARS_PER_TOKEN_ESTIMATE))


__all__ = ["OpenAICompatClient"]
