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
    ImagePart,
    LLMResponse,
    Message,
    MessageRole,
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
from netclient import lease_client

logger = structlog.get_logger(__name__)

_CHARS_PER_TOKEN_ESTIMATE: Final[float] = 4.0  # documented estimate — no real token-counting
# endpoint exists for these providers
# OpenRouter's own native web-search plugin — a raw tool-array entry, distinct from a
# function-tool built from `_tool_spec_to_wire`. Groq/Cerebras have no equivalent, so this is
# only ever appended when `provider_id == "openrouter"`.
_OPENROUTER_WEB_SEARCH_TOOL: Final[dict[str, str]] = {"type": "openrouter:web_search"}
_FINISH_REASON_MAP: Final[dict[str, StopReason]] = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
}

# The untyped JSON edges every `cast(...)` below narrows an httpx-decoded response value from —
# PEP-695 local aliases instead of repeating the same cast-target strings at every call site
# (this module's wire responses are dynamically-shaped JSON, not a typed SDK response).
type _JsonObject = dict[str, object]
type _JsonList = list[object]


def _image_content_part(image: ImagePart) -> dict[str, object]:
    """One `image_url` content part with a base64 data URL — OpenRouter's documented vision
    input shape for OpenAI-Chat-Completions-compatible wire format (this module is hand-rolled
    HTTP, not an SDK, so verified against OpenRouter's published multimodal-request docs
    rather than any installed SDK's types)."""
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{image.media_type};base64,{image.data}"},
    }


def _message_to_wire(message: Message) -> dict[str, object]:
    if message.role is MessageRole.TOOL:
        # OpenAI-compat represents each tool result as its own `role: tool` message.
        # Callers are expected to pass exactly one ToolResultPart per Message of this role;
        # multiple results become multiple Message entries upstream (llm/base.py docstring).
        part = message.tool_results[0]
        return {"role": "tool", "tool_call_id": part.tool_call_id, "content": part.content}
    content: object = message.content
    if message.images:
        # Content becomes a content-part list once an image is attached — the plain-string
        # shorthand `content` only covers text-only messages in this wire format.
        parts: list[dict[str, object]] = [_image_content_part(image) for image in message.images]
        if message.content:
            parts.append({"type": "text", "text": message.content})
        content = parts
    wire: dict[str, object] = {"role": message.role.value, "content": content}
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


def _detail(usage_raw: dict[str, object], group: str, field: str) -> int:
    details = usage_raw.get(group)
    if not isinstance(details, dict):
        return 0
    value = cast("_JsonObject", details).get(field)
    return int(value) if isinstance(value, int | float) and value > 0 else 0


def _cost_micros(usage_raw: dict[str, object]) -> int:
    """OpenRouter's usage accounting returns the call's charge as `usage.cost` (USD, a float)."""
    cost = usage_raw.get("cost")
    return round(float(cost) * 1_000_000) if isinstance(cost, int | float) and cost > 0 else 0


def _usage_from_raw(usage_raw: dict[str, object]) -> TokenUsage:
    return TokenUsage(
        input_tokens=int(cast("int", usage_raw.get("prompt_tokens", 0)) or 0),
        output_tokens=int(cast("int", usage_raw.get("completion_tokens", 0)) or 0),
        total_tokens=int(cast("int", usage_raw.get("total_tokens", 0)) or 0),
        cached_tokens=_detail(usage_raw, "prompt_tokens_details", "cached_tokens"),
        cache_write_tokens=_detail(usage_raw, "prompt_tokens_details", "cache_write_tokens"),
        reasoning_tokens=_detail(usage_raw, "completion_tokens_details", "reasoning_tokens"),
        cost_micros=_cost_micros(usage_raw),
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
    choice: dict[str, object], pending_calls: dict[int, dict[str, str]], *, provider_id: str
) -> list[TextDeltaEvent | ThinkingDeltaEvent | ToolCallDeltaEvent]:
    """Normalize one choice's `delta` object into zero or more typed events (helper split out
    of `stream()` to keep its branch/statement count under the ruff PLR0912/PLR0915 limits).

    Reasoning-delta text is OpenRouter-only (`delta.reasoning` per OpenRouter's published
    reasoning-tokens docs, or its `delta.reasoning_content` alias some upstream providers use
    instead) — Groq/Cerebras have no such field, and even if a raw chunk somehow carried one
    (a provider quirk, not a documented field), it must never surface as a `thinking_delta` for
    a non-OpenRouter provider, mirroring the `native_web_search` provider carve-out already in
    this module."""
    events: list[TextDeltaEvent | ThinkingDeltaEvent | ToolCallDeltaEvent] = []
    delta = cast(_JsonObject, choice.get("delta", {}))
    if provider_id == "openrouter":
        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            events.append(ThinkingDeltaEvent(text=reasoning))
    text = delta.get("content")
    if isinstance(text, str) and text:
        events.append(TextDeltaEvent(text=text))
    raw_tool_deltas = delta.get("tool_calls")
    if isinstance(raw_tool_deltas, list):
        for raw_delta in cast(_JsonList, raw_tool_deltas):
            if isinstance(raw_delta, dict):
                events.append(_apply_tool_call_delta(cast(_JsonObject, raw_delta), pending_calls))
    return events


def _url_citations_from_delta(delta: dict[str, object]) -> list[WebSearchSource]:
    """Extract `url_citation` annotations from one streamed delta — OpenRouter's own
    `openrouter:web_search` plugin shape (`delta.annotations`, verified live: one full
    annotation object per chunk, `{"type": "url_citation", "url_citation": {"url", "title",
    ...}}`), OpenRouter-only like the reasoning-delta carve-out above."""
    sources: list[WebSearchSource] = []
    raw_annotations = delta.get("annotations")
    if not isinstance(raw_annotations, list):
        return sources
    for raw in cast(_JsonList, raw_annotations):
        if not isinstance(raw, dict):
            continue
        annotation = cast(_JsonObject, raw)
        if annotation.get("type") != "url_citation":
            continue
        citation = annotation.get("url_citation")
        if not isinstance(citation, dict):
            continue
        citation_dict = cast(_JsonObject, citation)
        url = citation_dict.get("url")
        title = citation_dict.get("title")
        if isinstance(url, str) and isinstance(title, str):
            sources.append(WebSearchSource(url=url, title=title))
    return sources


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
    for raw in cast(_JsonList, raw_calls):
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
    events: list[TextDeltaEvent | ThinkingDeltaEvent | ToolCallDeltaEvent] = field(
        default_factory=list[TextDeltaEvent | ThinkingDeltaEvent | ToolCallDeltaEvent]
    )
    stop_reason: StopReason | None = None
    usage: TokenUsage | None = None
    web_search_sources: list[WebSearchSource] = field(default_factory=list[WebSearchSource])


def _parse_sse_line(
    line: str, pending_calls: dict[int, dict[str, str]], *, provider_id: str
) -> _SseLineResult:
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
    delta = cast(_JsonObject, choice.get("delta", {}))
    return _SseLineResult(
        events=_events_from_delta(choice, pending_calls, provider_id=provider_id),
        stop_reason=stop_reason,
        usage=usage,
        web_search_sources=(
            _url_citations_from_delta(delta) if provider_id == "openrouter" else []
        ),
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
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        *,
        stream: bool,
        native_web_search: bool,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self.model_id,
            "messages": [_message_to_wire(m) for m in messages],
            "stream": stream,
        }
        wire_tools = [_tool_spec_to_wire(t) for t in tools] if tools else []
        if native_web_search and self.provider_id == "openrouter":
            wire_tools.append(dict(_OPENROUTER_WEB_SEARCH_TOOL))
        if wire_tools:
            body["tools"] = wire_tools
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        body = self._body(messages, tools, stream=False, native_web_search=native_web_search)
        async with lease_client(timeout=self._timeout) as client:
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
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[
        TextDeltaEvent
        | ThinkingDeltaEvent
        | ToolCallDeltaEvent
        | ToolCallEvent
        | WebSearchResultEvent
        | StreamDoneEvent
    ]:
        body = self._body(messages, tools, stream=True, native_web_search=native_web_search)
        # Assembles partial tool-call argument fragments keyed by index (OpenAI-compat streams
        # tool calls by array index, filling in id/name once then appending arguments deltas).
        pending_calls: dict[int, dict[str, str]] = {}
        stop_reason = StopReason.END_TURN
        usage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
        # OpenRouter streams one full `url_citation` annotation per chunk — accumulated across
        # the whole response and emitted as ONE `WebSearchResultEvent` right before the
        # terminal `StreamDoneEvent`, mirroring the Anthropic/Gemini clients' own "whole,
        # once complete" web-search-result event shape rather than a delta-per-source stream.
        web_search_sources: list[WebSearchSource] = []

        async with (
            lease_client(timeout=self._timeout) as client,
            client.stream(
                "POST", f"{self._base_url}/chat/completions", headers=self._headers(), json=body
            ) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                result = _parse_sse_line(line, pending_calls, provider_id=self.provider_id)
                if result.done:
                    break
                if result.usage is not None:
                    usage = result.usage
                if result.stop_reason is not None:
                    stop_reason = result.stop_reason
                web_search_sources.extend(result.web_search_sources)
                for event in result.events:
                    yield event

        for slot in pending_calls.values():
            yield ToolCallEvent(
                tool_call=ToolCall(
                    id=slot["id"], name=slot["name"], arguments=_parse_json_object(slot["args"])
                )
            )
        if web_search_sources:
            yield WebSearchResultEvent(sources=tuple(web_search_sources))
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
