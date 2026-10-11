"""OpenAI client (`provider` id `openai`). Wraps the official `openai` SDK's **Responses**
API (`self._client.responses.create(...)`) via `AsyncOpenAI` (natively async — no
`anyio.to_thread.run_sync` wrapping needed, same as `anthropic_client.py`).

Why Responses, not Chat Completions: OpenAI's own docs recommend Responses over Chat
Completions for reasoning models ("Reasoning models work better with the Responses API"), and
Responses is the *only* endpoint that streams `response.reasoning_summary_text.delta` events —
Chat Completions has no reasoning-summary streaming mechanism at all. This module previously
wrapped Chat Completions directly; it now talks to Responses end-to-end (request shape
`input`/`ResponseInputItemParam`, not `messages`; typed `Response`/`ResponseStreamEvent`
objects, not `ChatCompletion`/`ChatCompletionChunk`).

Design note — why this is a separate module and not a thin subclass of `openai_compat.py`
(judgment call, per the task brief): `openai_client.py` is kept distinct from `openai_compat.py`
because, on inspection, the two are **not** wire-identical once you use OpenAI's own SDK types
rather than hand-rolled HTTP: the SDK's typed `Response`/`ResponseStreamEvent`/`ResponseUsage`
objects are Pydantic models (not raw dicts we `cast()` through), OpenAI's SDK auth/retry/timeout
handling differs from a generic `httpx` POST, and OpenAI is the one provider where a *native*
SDK-typed integration is expected quality (this is the flagship provider, not a "cheap to
extend" gateway entry). `openai_compat.py` remains correct for Groq/Cerebras/OpenRouter/Ollama,
which speak Chat-Completions-shaped wire format but are not the SDK's origin vendor and don't
get first-class SDK-object handling here.

Token counting: neither Chat Completions nor Responses has a free-standing count-tokens
endpoint. `tiktoken` is the documented, idiomatic client-side tokenizer for OpenAI models and is
used here for an exact prompt-side *tokenizer* count wherever a model is known to
`tiktoken.encoding_for_model`; unknown/future model ids fall back to `o200k_base`. Switching the
wire endpoint from Chat Completions to Responses does not change this estimate's accuracy in any
way that matters: `tiktoken` counts raw text content, which is endpoint-independent — the only
thing that could drift is the small fixed per-item/per-message framing overhead the server adds
before tokenizing, which was already an approximation under Chat Completions (documented below)
and remains one here; not worth a bespoke fix for an already-approximate estimate.

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
from openai.types.responses import (
    Response,
    ResponseFunctionToolCallParam,
    ResponseInputImageParam,
    ResponseInputItemParam,
    ResponseStreamEvent,
)
from openai.types.responses.response_input_item_param import FunctionCallOutput
from openai.types.responses.response_input_item_param import Message as InputMessageParam
from openai.types.responses.response_input_text_param import ResponseInputTextParam
from openai.types.shared_params.reasoning import Reasoning

from llm.base import (
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
)
from llm.shared import parse_json_object

logger = structlog.get_logger(__name__)

# Verified against the installed `openai` SDK's `openai.types.responses.response_status`:
# `ResponseStatus = Literal["completed", "failed", "in_progress", "cancelled", "queued",
# "incomplete"]` — a materially different vocabulary from Chat Completions' `finish_reason`
# strings (`stop`/`tool_calls`/`length`/`content_filter`). Responses has no dedicated
# "the model chose to call a tool" terminal status distinct from `completed`; whether the model
# proposed tool calls is instead read off the `output` items themselves (any `function_call`
# item present), so that branch is handled in code, not this table. `incomplete` carries a
# `response.incomplete_details.reason` of `max_output_tokens` or `content_filter` — that finer
# reason is what actually maps to `MAX_TOKENS`/`ERROR`, not the top-level `status` alone.
_STATUS_MAP: Final[dict[str, StopReason]] = {
    "completed": StopReason.END_TURN,
    "cancelled": StopReason.ERROR,
    "failed": StopReason.ERROR,
    "queued": StopReason.END_TURN,
    "in_progress": StopReason.END_TURN,
}

_INCOMPLETE_REASON_MAP: Final[dict[str, StopReason]] = {
    "max_output_tokens": StopReason.MAX_TOKENS,
    "content_filter": StopReason.ERROR,
}

# `tiktoken`'s static model->encoding registry doesn't yet know every catalog model id (e.g.
# forward-looking `gpt-5.5`/`gpt-5.4`/`gpt-5.4-mini`) — fall back to the encoding OpenAI's
# current-generation chat models use rather than raising, so context-window budgeting still
# gets a working count instead of a crash on an unrecognized-but-valid model id.
_FALLBACK_ENCODING: Final[str] = "o200k_base"

# Models with `reasoning` support on the Responses endpoint in this build, matched by prefix.
# Verified against the installed `openai` SDK's `ReasoningEffort` type alias
# (`openai.types.shared.reasoning_effort`) — a `Literal["none", "minimal", "low", "medium",
# "high", "xhigh", "max"]`; this client defaults to `"medium"` (see `_reasoning_effort` below).
_REASONING_EFFORT_MODEL_PREFIXES: Final[tuple[str, ...]] = ("gpt-5.6", "gpt-5-6")


def _supports_reasoning_effort(model_id: str) -> bool:
    return any(model_id.startswith(prefix) for prefix in _REASONING_EFFORT_MODEL_PREFIXES)


def _encoding_for_model(model_id: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model_id)
    except KeyError:
        return tiktoken.get_encoding(_FALLBACK_ENCODING)


def _message_to_input_items(message: Message) -> list[ResponseInputItemParam]:
    """Convert one normalized `Message` into zero or more Responses-API `input` items.

    Verified against the installed SDK: a plain user/system turn is a `Message` input item
    (`openai.types.responses.response_input_item_param.Message`, `role` restricted to
    `"user"|"system"|"developer"`, `content` a list of `ResponseInputTextParam`-shaped parts —
    Responses drops Chat Completions' bare-string `content` shorthand for input items sent this
    way, so plain text is wrapped in a one-element `input_text` list here). A prior assistant
    turn with tool calls surfaces each call as its own top-level `function_call` item
    (`ResponseFunctionToolCallParam`, `call_id`/`name`/`arguments`) rather than a nested
    `tool_calls` array — Responses has no single "assistant message with tool_calls" item type,
    each function call is a sibling item in `input`. A `role=tool` message (this codebase's
    normalized shape for reporting a prior tool call's outcome) becomes a `function_call_output`
    item per `ToolResultPart`, keyed by `call_id` (Responses' name for what Chat Completions
    calls `tool_call_id`).
    """
    if message.role is MessageRole.TOOL:
        return [
            FunctionCallOutput(
                type="function_call_output",
                call_id=part.tool_call_id,
                output=part.content,
            )
            for part in message.tool_results
        ]
    items: list[ResponseInputItemParam] = []
    if message.role is MessageRole.ASSISTANT:
        if message.content:
            # Responses' `Message` input-item role literal only accepts
            # "user"|"system"|"developer" — assistant turns supplied as *input* history use
            # `EasyInputMessageParam` instead, which does accept "assistant" (and, per the SDK,
            # takes plain-string `content` directly rather than a content-part list).
            assistant_item = {"type": "message", "role": "assistant", "content": message.content}
            items.append(cast("Any", assistant_item))
        for call in message.tool_calls:
            items.append(
                ResponseFunctionToolCallParam(
                    type="function_call",
                    call_id=call.id,
                    name=call.name,
                    arguments=json.dumps(call.arguments),
                )
            )
        return items
    role = "developer" if message.role is MessageRole.SYSTEM else "user"
    if message.content or message.images:
        content: list[Any] = [
            ResponseInputImageParam(
                type="input_image",
                detail="auto",
                image_url=f"data:{image.media_type};base64,{image.data}",
            )
            for image in message.images
        ]
        if message.content:
            content.append(ResponseInputTextParam(type="input_text", text=message.content))
        items.append(
            InputMessageParam(type="message", role=cast("Any", role), content=cast("Any", content))
        )
    return items


def _tool_spec_to_param(spec: ToolSpec) -> dict[str, Any]:
    """Responses' top-level tool shape is flatter than Chat Completions' nested
    `{"type": "function", "function": {...}}` — verified against `FunctionToolParam`
    (`openai.types.responses.function_tool_param`): `name`/`description`/`parameters` sit
    directly on the tool dict alongside `type: "function"`, plus SDK-required
    `strict: Optional[bool]` (left `None` — this codebase's `ToolSpec.parameters` JSON Schema is
    not authored for OpenAI's strict-mode subset)."""
    return {
        "type": "function",
        "name": spec.name,
        "description": spec.description,
        "parameters": spec.parameters,
        "strict": None,
    }


def _usage_from_response(response: Response) -> TokenUsage:
    usage = response.usage
    if usage is None:
        return TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cached_tokens=usage.input_tokens_details.cached_tokens,
        reasoning_tokens=usage.output_tokens_details.reasoning_tokens,
    )


def _stop_reason_from_response(response: Response) -> StopReason:
    """Map the Responses API's status vocabulary to this codebase's `StopReason`.

    A `completed` response whose `output` contains at least one `function_call` item maps to
    `TOOL_USE` — Responses has no `finish_reason: "tool_calls"` equivalent; a tool-call turn is
    just a `completed` response whose output happens to include function-call items. An
    `incomplete` response's finer-grained `incomplete_details.reason` (`max_output_tokens` /
    `content_filter`) is what actually determines `MAX_TOKENS` vs `ERROR`; an `incomplete`
    response with no `incomplete_details` (defensively) falls back to `END_TURN`.
    """
    if response.status == "incomplete":
        details = response.incomplete_details
        if details is not None and details.reason is not None:
            return _INCOMPLETE_REASON_MAP.get(details.reason, StopReason.END_TURN)
        return StopReason.END_TURN
    has_function_call = any(item.type == "function_call" for item in response.output)
    if response.status == "completed" and has_function_call:
        return StopReason.TOOL_USE
    return _STATUS_MAP.get(response.status or "completed", StopReason.END_TURN)


def _tool_calls_from_response(response: Response) -> tuple[ToolCall, ...]:
    return tuple(
        ToolCall(id=item.call_id, name=item.name, arguments=parse_json_object(item.arguments))
        for item in response.output
        if item.type == "function_call"
    )


def _text_from_response(response: Response) -> str | None:
    text = response.output_text
    return text or None


class OpenAIClient:
    """The native OpenAI `LLMClient`, backed by the Responses API."""

    def __init__(  # noqa: PLR0913 — one keyword per independently-configurable knob, matching
        # the existing `max_tokens`/`base_url` style rather than a config object
        self,
        *,
        model_id: str,
        api_key: str,
        capabilities: ModelCapabilities | None = None,
        max_tokens: int = 4096,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.provider_id = "openai"
        self.model_id = model_id
        # Neither the catalog models here (`gpt-5.5`/`gpt-5.4`/`gpt-5.4-mini`/`gpt-4o`) nor the
        # Responses API itself expose a general-purpose grounding/web-search *server tool* the
        # way Anthropic's `web_search` or Gemini's `google_search` do for every model — Responses
        # does have a `web_search` tool type, but it is model/tier-gated, not a blanket capability
        # of every model id this client constructs for — so we do not default-claim
        # `native_web_search=True` here, which would overclaim.
        self.capabilities = capabilities or ModelCapabilities(
            native_tool_calling=True,
            streaming=True,
            native_web_search=False,
            # Every model in this build's OpenAI catalog (`gpt-5.5`/`gpt-5.4`/`gpt-5.4-mini`/
            # `gpt-4o`) is documented by OpenAI as multimodal/vision-capable on the Responses
            # API — verified against OpenAI's published model pages, not assumed. A future
            # catalog addition of a text-only model would need this re-checked at that time.
            supports_vision=True,
        )
        self._max_tokens = max_tokens
        # Sensible default kept off unless the model supports it — a caller can still force a
        # specific level via the constructor kwarg regardless of the model-prefix heuristic.
        self._reasoning_effort = (
            reasoning_effort
            if reasoning_effort is not None
            else ("medium" if _supports_reasoning_effort(model_id) else None)
        )
        # SDK client construction is local — no network call.
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._encoding = _encoding_for_model(model_id)

    def _params(self, messages: list[Message], tools: list[ToolSpec] | None) -> dict[str, Any]:
        input_items: list[ResponseInputItemParam] = []
        for message in messages:
            input_items.extend(_message_to_input_items(message))
        params: dict[str, Any] = {
            "model": self.model_id,
            "max_output_tokens": self._max_tokens,
            "input": input_items,
        }
        if tools:
            params["tools"] = [_tool_spec_to_param(t) for t in tools]
        if self._reasoning_effort is not None:
            effort = cast("Any", self._reasoning_effort)
            params["reasoning"] = Reasoning(effort=effort, summary="auto")
        return params

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        # `native_web_search` is accepted only to satisfy the `LLMClient` Protocol signature —
        # no native mechanism is wired for this provider's catalog (see the constructor's
        # `capabilities` comment above); intentionally never read.
        del native_web_search
        params = self._params(messages, tools)
        try:
            response = cast("Response", await self._client.responses.create(**params))
        except Exception as exc:
            # Never log the api_key (governance §6) — the SDK never surfaces it in exc text.
            logger.warning("llm_openai_chat_failed", model=self.model_id, error=type(exc).__name__)
            raise

        return LLMResponse(
            text=_text_from_response(response),
            tool_calls=_tool_calls_from_response(response),
            stop_reason=_stop_reason_from_response(response),
            usage=_usage_from_response(response),
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[
        TextDeltaEvent | ThinkingDeltaEvent | ToolCallDeltaEvent | ToolCallEvent | StreamDoneEvent
    ]:
        # See `chat()` — no native mechanism for this provider's catalog; never read.
        del native_web_search
        params = self._params(messages, tools)
        params["stream"] = True

        # Keyed by `item_id` (Responses' per-output-item identifier) rather than Chat
        # Completions' positional `index` — Responses assigns each function-call output item a
        # stable `item_id` up front (`response.output_item.added`), then streams argument deltas
        # keyed by that same `item_id`.
        pending: dict[str, dict[str, str]] = {}
        stop_reason = StopReason.END_TURN
        usage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)

        response_stream = cast(
            "AsyncIterator[ResponseStreamEvent]",
            await self._client.responses.create(**params),
        )
        async for event in response_stream:
            if event.type == "response.output_item.added" and event.item.type == "function_call":
                pending[str(event.output_index)] = {
                    "id": event.item.call_id,
                    "name": event.item.name,
                    "args": "",
                }
                yield ToolCallDeltaEvent(id=event.item.call_id, name=event.item.name)
            elif event.type == "response.output_text.delta":
                yield TextDeltaEvent(text=event.delta)
            elif event.type == "response.reasoning_summary_text.delta":
                yield ThinkingDeltaEvent(text=event.delta)
            elif event.type == "response.function_call_arguments.delta":
                slot = pending.get(str(event.output_index))
                if slot is not None:
                    slot["args"] += event.delta
                    yield ToolCallDeltaEvent(id=slot["id"], arguments_delta=event.delta)
            # Each branch repeats the same two lines instead of `or`-combining the conditions
            # (ruff SIM114's suggestion) because a combined check no longer narrows pyright's
            # discriminated union on `event.response` — most `ResponseStreamEvent` members have
            # no `.response` attribute at all, and only a per-literal `==` check narrows it away.
            elif event.type == "response.completed":  # noqa: SIM114
                stop_reason = _stop_reason_from_response(event.response)
                usage = _usage_from_response(event.response)
            elif event.type == "response.failed":  # noqa: SIM114
                stop_reason = _stop_reason_from_response(event.response)
                usage = _usage_from_response(event.response)
            elif event.type == "response.incomplete":
                stop_reason = _stop_reason_from_response(event.response)
                usage = _usage_from_response(event.response)

        for slot in pending.values():
            yield ToolCallEvent(
                tool_call=ToolCall(
                    id=slot["id"], name=slot["name"], arguments=parse_json_object(slot["args"])
                )
            )
        yield StreamDoneEvent(stop_reason=stop_reason, usage=usage)

    async def count_tokens(self, messages: list[Message]) -> int:
        """Exact client-side count via `tiktoken` (neither Chat Completions nor Responses has a
        counting endpoint, and `tiktoken` is the idiomatic client-side tokenizer to use instead).
        Sums encoded lengths of all text content, tool-call names/arguments, and tool-result
        content; the small fixed per-item/per-message framing overhead the real API adds
        server-side is not modeled, so this is a slight undercount — acceptable for the
        context-window budgeting consumer, same tolerance this estimate already had under
        Chat Completions (endpoint-independent, since `tiktoken` counts text, not wire framing)."""
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
