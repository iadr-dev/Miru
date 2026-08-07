"""OpenAI client (`provider` id `openai`). Wraps the official
`openai` SDK's Chat Completions API directly with `AsyncOpenAI` (natively async — no
`anyio.to_thread.run_sync` wrapping needed, same as `anthropic_client.py`).

Design note — why this is a separate module and not a thin subclass of `openai_compat.py`
(judgment call, per the task brief): `openai_client.py` is kept distinct from
`openai_compat.py` because, on inspection, the two are **not** wire-
identical once you use OpenAI's own SDK types rather than hand-rolled HTTP: the SDK's
`ChatCompletionMessageToolCall`/`ChatCompletionChunk`/`CompletionUsage` are typed Pydantic
models (not raw dicts we `cast()` through), OpenAI's SDK auth/retry/timeout handling differs
from a generic `httpx` POST, and OpenAI is the one provider where a *native* SDK-typed
integration is expected quality (this is the flagship provider, not a "cheap to extend"
gateway entry). `openai_compat.py` remains correct for Groq/Cerebras/OpenRouter/Ollama, which
speak the same *wire* format but are not the SDK's origin vendor and don't get first-class
SDK-object handling here. Wire *shape* (roles, function-calling JSON) is naturally similar to
`openai_compat.py` since OpenAI originated it — but this module talks to the SDK's typed
objects end-to-end, not JSON dicts, which is the actual differentiator being pointed
at (a native tool-call format per provider, normalized in the adapter).

Token counting: OpenAI's Chat Completions API has no free-standing count-tokens endpoint.
`tiktoken` is the documented, idiomatic client-side tokenizer for OpenAI models and is used
here for an exact prompt-side token count (not a heuristic) wherever a model is known to
`tiktoken.encoding_for_model`; unknown/future model ids (e.g. `gpt-5.5`/`gpt-5.4` not yet in
tiktoken's static registry as of this build) fall back to the `o200k_base` encoding — the
encoding OpenAI's own newer chat models (`gpt-4o` and later) use — which is exact for the
*tokenizer*, though the small per-message/tool-call framing overhead OpenAI's server adds is
still an approximation (documented below).

Construction never touches the network — only `chat()`/`stream()`/`count_tokens()` do
(`count_tokens()` is fully local/offline here, no API call at all).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Final, cast

import structlog
import tiktoken
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

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

_FINISH_REASON_MAP: Final[dict[str, StopReason]] = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.ERROR,
}

# `tiktoken`'s static model->encoding registry doesn't yet know every catalog model id (e.g.
# forward-looking `gpt-5.5`/`gpt-5.4`/`gpt-5.4-mini`) — fall back to the encoding OpenAI's
# current-generation chat models use rather than raising, so context-window budgeting still
# gets a working count instead of a crash on an unrecognized-but-valid model id.
_FALLBACK_ENCODING: Final[str] = "o200k_base"


def _encoding_for_model(model_id: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model_id)
    except KeyError:
        return tiktoken.get_encoding(_FALLBACK_ENCODING)


def _message_to_param(message: Message) -> dict[str, Any]:
    if message.role is MessageRole.TOOL:
        part = message.tool_results[0]
        return {"role": "tool", "tool_call_id": part.tool_call_id, "content": part.content}
    param: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.role is MessageRole.ASSISTANT and message.tool_calls:
        param["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    return param


def _tool_spec_to_param(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def _parse_json_object(raw: str) -> dict[str, object]:
    """Parse a (possibly empty/malformed) assembled tool-call-arguments JSON fragment into a
    typed `dict[str, object]`, never raising and never letting an `Any` escape this module."""
    if not raw:
        return {}
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return cast("dict[str, object]", parsed) if isinstance(parsed, dict) else {}


def _usage_from_completion(completion: ChatCompletion) -> TokenUsage:
    usage = completion.usage
    if usage is None:
        return TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
    return TokenUsage(
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )


def _apply_tool_call_delta(
    raw_delta: Any, pending: dict[int, dict[str, str]]
) -> ToolCallDeltaEvent:
    """Fold one raw SDK `ChoiceDeltaToolCall` chunk into `pending` and return the normalized
    delta event (helper split out to keep `stream()`'s branch count under ruff's PLR limits)."""
    index = raw_delta.index
    slot = pending.setdefault(index, {"id": "", "name": "", "args": ""})
    if raw_delta.id:
        slot["id"] = raw_delta.id
    name_delta: str | None = None
    args_delta: str | None = None
    fn = raw_delta.function
    if fn is not None:
        if fn.name:
            slot["name"] = fn.name
            name_delta = fn.name
        if fn.arguments:
            slot["args"] += fn.arguments
            args_delta = fn.arguments
    return ToolCallDeltaEvent(id=slot["id"], name=name_delta, arguments_delta=args_delta)


def _events_from_chunk(
    chunk: ChatCompletionChunk, pending: dict[int, dict[str, str]]
) -> list[TextDeltaEvent | ToolCallDeltaEvent]:
    """Normalize one streamed chunk's first choice into zero or more typed events (helper
    split out to keep `stream()`'s branch/statement count under ruff's PLR limits)."""
    if not chunk.choices:
        return []
    delta = chunk.choices[0].delta
    events: list[TextDeltaEvent | ToolCallDeltaEvent] = []
    if delta.content:
        events.append(TextDeltaEvent(text=delta.content))
    if delta.tool_calls:
        events.extend(_apply_tool_call_delta(raw, pending) for raw in delta.tool_calls)
    return events


class OpenAIClient:
    """The native OpenAI `LLMClient`."""

    def __init__(
        self,
        *,
        model_id: str,
        api_key: str,
        capabilities: ModelCapabilities | None = None,
        max_tokens: int = 4096,
        base_url: str | None = None,
    ) -> None:
        self.provider_id = "openai"
        self.model_id = model_id
        # OpenAI's Chat Completions API has no dedicated grounding/web-search tool for the
        # catalog models here (`gpt-5.5`/`gpt-5.4`/`gpt-5.4-mini`/`gpt-4o`) in the way Anthropic's
        # `web_search` server tool or Gemini's `google_search` grounding tool work — the
        # `web_search_options` param exists on OpenAI's *Responses* API / certain search-preview
        # model variants, not as a general capability of every catalog model id here, so we do
        # not default-claim `native_web_search=True` — that would overclaim a capability the
        # API doesn't actually have for the listed models.
        self.capabilities = capabilities or ModelCapabilities(
            native_tool_calling=True, streaming=True, native_web_search=False
        )
        self._max_tokens = max_tokens
        # SDK client construction is local — no network call.
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._encoding = _encoding_for_model(model_id)

    def _params(self, messages: list[Message], tools: list[ToolSpec] | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model_id,
            "max_completion_tokens": self._max_tokens,
            "messages": [_message_to_param(m) for m in messages],
        }
        if tools:
            params["tools"] = [_tool_spec_to_param(t) for t in tools]
        return params

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        params = self._params(messages, tools)
        try:
            completion = cast(
                "ChatCompletion", await self._client.chat.completions.create(**params)
            )
        except Exception as exc:
            # Never log the api_key (governance §6) — the SDK never surfaces it in exc text.
            logger.warning("llm_openai_chat_failed", model=self.model_id, error=type(exc).__name__)
            raise

        choice = completion.choices[0]
        message = choice.message
        tool_calls = tuple(
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=_parse_json_object(call.function.arguments),
            )
            for call in (message.tool_calls or [])
            if call.type == "function"
        )
        stop_reason = _FINISH_REASON_MAP.get(choice.finish_reason, StopReason.END_TURN)
        return LLMResponse(
            text=message.content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=_usage_from_completion(completion),
        )

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[TextDeltaEvent | ToolCallDeltaEvent | ToolCallEvent | StreamDoneEvent]:
        params = self._params(messages, tools)
        params["stream"] = True
        params["stream_options"] = {"include_usage": True}

        pending: dict[int, dict[str, str]] = {}
        stop_reason = StopReason.END_TURN
        usage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)

        response = cast(
            "AsyncIterator[ChatCompletionChunk]",
            await self._client.chat.completions.create(**params),
        )
        async for chunk in response:
            for event in _events_from_chunk(chunk, pending):
                yield event
            if chunk.choices and chunk.choices[0].finish_reason:
                stop_reason = _FINISH_REASON_MAP.get(
                    chunk.choices[0].finish_reason, StopReason.END_TURN
                )
            if chunk.usage is not None:
                usage = TokenUsage(
                    input_tokens=chunk.usage.prompt_tokens,
                    output_tokens=chunk.usage.completion_tokens,
                    total_tokens=chunk.usage.total_tokens,
                )

        for slot in pending.values():
            yield ToolCallEvent(
                tool_call=ToolCall(
                    id=slot["id"], name=slot["name"], arguments=_parse_json_object(slot["args"])
                )
            )
        yield StreamDoneEvent(stop_reason=stop_reason, usage=usage)

    async def count_tokens(self, messages: list[Message]) -> int:
        """Exact client-side count via `tiktoken` (no OpenAI Chat Completions counting
        endpoint exists, and `tiktoken` is the idiomatic client-side tokenizer to use instead).
        Sums encoded lengths of all text content, tool-call names/arguments, and tool-result
        content; the small fixed per-message role/name framing overhead the real API adds
        server-side is not modeled, so this is a slight (typically 3-4 tokens/message)
        undercount — acceptable for the context-window budgeting consumer, same tolerance as
        the openai-compat heuristic it replaces with an exact tokenizer count."""
        total = 0
        for message in messages:
            if message.content:
                total += len(self._encoding.encode(message.content))
            for call in message.tool_calls:
                total += len(self._encoding.encode(call.name))
                total += len(self._encoding.encode(json.dumps(call.arguments)))
            for result in message.tool_results:
                total += len(self._encoding.encode(result.content))
        return max(1, total)


__all__ = ["OpenAIClient"]
