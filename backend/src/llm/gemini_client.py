"""Google Gemini client (`provider` id `gemini`). Wraps the
current `google-genai` SDK (the unified Gemini Developer API / Vertex AI client that replaced
the deprecated `google-generativeai` package — verified via `uv add google-genai` per the task
brief rather than assumed).

The SDK's `Client(...).aio.models` surface is natively async (`generate_content`,
`generate_content_stream`, `count_tokens` all live on the `aio` accessor) — no
`anyio.to_thread.run_sync` wrapping needed, same reasoning as `anthropic_client.py`.

Construction never touches the network — `genai.Client(api_key=...)` is local; only
`chat()`/`stream()`/`count_tokens()` call the Gemini API.

Judgment call — tool-result → `name` round-trip: Gemini's `FunctionResponse` part must carry
the function **name**, not just an opaque call id (unlike Anthropic's `tool_use_id`-only
`tool_result` block or OpenAI's `tool_call_id`-only `tool` message). `llm/base.py`'s
`ToolResultPart` only carries `tool_call_id` + `content` (by design — the adapter's normalized
wire format doesn't know the tool's name at that point in the base module). To bridge this
without widening `base.py`, this module scans the conversation's prior ASSISTANT `tool_calls`
for the matching `id` and recovers the `name` before building the `function_response` part;
this is purely a local, in-memory lookup over the messages already passed to `chat`/`stream`,
not a new normalized type.

Judgment call — tool-call id synthesis: Gemini's `FunctionCall.id` is optional and typically
absent outside a subset of tool-use modes; `ToolCall.id` in `llm/base.py` is required. Where
the SDK gives no id we synthesize a deterministic one (`f"{name}_{index}"`) — safe because
Gemini identifies a function call by `name` (matched via the lookup above) when the *result*
is threaded back, never by this synthetic id.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Final, cast

import structlog
from google import genai
from google.genai import types as genai_types

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
    ToolResultPart,
    ToolSpec,
    WebSearchResultEvent,
    WebSearchSource,
)

logger = structlog.get_logger(__name__)

# Models with thinking-config support in this build's catalog, matched by prefix so a dated
# snapshot id under either family still qualifies — verified against the installed
# `google-genai` SDK's `ThinkingConfig` (`include_thoughts`/`thinking_budget`/`thinking_level`)
# and `Part.thought` (the streamed-part flag distinguishing reasoning text from answer text).
_EXTENDED_THINKING_MODEL_PREFIXES: Final[tuple[str, ...]] = ("gemini-3", "gemini-2.5")


def _supports_extended_thinking(model_id: str) -> bool:
    return any(model_id.startswith(prefix) for prefix in _EXTENDED_THINKING_MODEL_PREFIXES)


_FINISH_REASON_MAP: Final[dict[genai_types.FinishReason, StopReason]] = {
    genai_types.FinishReason.STOP: StopReason.END_TURN,
    genai_types.FinishReason.MAX_TOKENS: StopReason.MAX_TOKENS,
    genai_types.FinishReason.SAFETY: StopReason.ERROR,
    genai_types.FinishReason.RECITATION: StopReason.ERROR,
    genai_types.FinishReason.MALFORMED_FUNCTION_CALL: StopReason.ERROR,
}


def _build_tool_call_name_map(messages: list[Message]) -> dict[str, str]:
    """Recover `tool_call_id -> name` from every prior ASSISTANT `tool_calls` entry — needed
    to build a `function_response` part (see module docstring)."""
    mapping: dict[str, str] = {}
    for message in messages:
        if message.role is MessageRole.ASSISTANT:
            for call in message.tool_calls:
                mapping[call.id] = call.name
    return mapping


def _split_system(messages: list[Message]) -> tuple[str | None, list[Message]]:
    """Gemini takes `system_instruction` as a top-level config param, not a `Content` entry."""
    system_parts = [m.content for m in messages if m.role is MessageRole.SYSTEM and m.content]
    turns = [m for m in messages if m.role is not MessageRole.SYSTEM]
    system = "\n\n".join(system_parts) if system_parts else None
    return system, turns


def _tool_result_part(part: ToolResultPart, name_map: dict[str, str]) -> genai_types.Part:
    name = name_map.get(part.tool_call_id, part.tool_call_id)
    response: dict[str, object] = {"content": part.content}
    if part.is_error:
        response["error"] = True
    return genai_types.Part.from_function_response(name=name, response=response)


def _image_part(image: ImagePart) -> genai_types.Part:
    """Verified against the installed `google-genai` SDK's `Part.from_bytes(data: bytes,
    mime_type: str)` — this SDK method wants raw bytes, not base64 text, so the normalized
    `ImagePart.data` (base64) is decoded here before handing it to the SDK."""
    return genai_types.Part.from_bytes(
        data=base64.b64decode(image.data), mime_type=image.media_type
    )


def _message_to_content(message: Message, name_map: dict[str, str]) -> genai_types.Content:
    if message.role is MessageRole.TOOL:
        parts = [_tool_result_part(part, name_map) for part in message.tool_results]
        return genai_types.Content(role="user", parts=parts)
    if message.role is MessageRole.ASSISTANT and message.tool_calls:
        assistant_parts: list[genai_types.Part] = []
        if message.content:
            assistant_parts.append(genai_types.Part.from_text(text=message.content))
        assistant_parts.extend(
            genai_types.Part.from_function_call(name=call.name, args=dict(call.arguments))
            for call in message.tool_calls
        )
        return genai_types.Content(role="model", parts=assistant_parts)
    role = "model" if message.role is MessageRole.ASSISTANT else "user"
    if message.images:
        parts = [_image_part(image) for image in message.images]
        if message.content:
            parts.append(genai_types.Part.from_text(text=message.content))
        return genai_types.Content(role=role, parts=parts)
    text_part = genai_types.Part.from_text(text=message.content or "")
    return genai_types.Content(role=role, parts=[text_part])


def _tool_spec_to_declaration(spec: ToolSpec) -> genai_types.FunctionDeclaration:
    return genai_types.FunctionDeclaration(
        name=spec.name, description=spec.description, parameters_json_schema=spec.parameters
    )


def _usage_from_metadata(
    metadata: genai_types.GenerateContentResponseUsageMetadata | None,
) -> TokenUsage:
    if metadata is None:
        return TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
    input_tokens = metadata.prompt_token_count or 0
    output_tokens = metadata.candidates_token_count or 0
    total_tokens = metadata.total_token_count or (input_tokens + output_tokens)
    return TokenUsage(
        input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens
    )


def _function_call_to_tool_call(call: genai_types.FunctionCall, index: int) -> ToolCall:
    return ToolCall(
        id=call.id or f"{call.name}_{index}",
        name=call.name or "",
        arguments=dict(call.args or {}),
    )


def _events_from_chunk_parts(
    parts: list[genai_types.Part], emitted_tool_calls: int
) -> tuple[list[TextDeltaEvent | ThinkingDeltaEvent | ToolCallDeltaEvent | ToolCallEvent], int]:
    """One chunk's parts turned into events, plus the running tool-call count (Gemini yields
    each function call whole, so a tool call surfaces as one `ToolCallDeltaEvent` + immediate
    `ToolCallEvent`, never assembled incrementally like Anthropic/OpenAI's argument chunks).
    A part with `thought is True` (verified against the installed SDK's `Part.thought: bool |
    None` field) carries reasoning text, not final-answer text, and becomes a
    `ThinkingDeltaEvent` instead of a `TextDeltaEvent`. Pulled out of `stream()` purely to keep
    that method's own branching shallow."""
    events: list[TextDeltaEvent | ThinkingDeltaEvent | ToolCallDeltaEvent | ToolCallEvent] = []
    for part in parts:
        if part.text and part.thought:
            events.append(ThinkingDeltaEvent(text=part.text))
        elif part.text:
            events.append(TextDeltaEvent(text=part.text))
        elif part.function_call is not None:
            call = _function_call_to_tool_call(part.function_call, emitted_tool_calls)
            emitted_tool_calls += 1
            events.append(
                ToolCallDeltaEvent(
                    id=call.id, name=call.name, arguments_delta=json.dumps(call.arguments)
                )
            )
            events.append(ToolCallEvent(tool_call=call))
    return events, emitted_tool_calls


def _web_search_result_event(
    metadata: genai_types.GroundingMetadata | None,
) -> WebSearchResultEvent | None:
    """Build a `WebSearchResultEvent` from one chunk's `grounding_metadata`, if present —
    verified live: `grounding_chunks[].web.title`/`.uri` are populated real values (a
    provider-domain title + a redirect-wrapped source URL), arriving as one complete blob on
    a single late chunk of the stream, not incrementally per-chunk like text/tool-call deltas."""
    if metadata is None or not metadata.grounding_chunks:
        return None
    sources = tuple(
        WebSearchSource(url=chunk.web.uri, title=chunk.web.title)
        for chunk in metadata.grounding_chunks
        if chunk.web is not None and chunk.web.uri and chunk.web.title
    )
    return WebSearchResultEvent(sources=sources) if sources else None


def _chunk_stop_reason(
    candidate: genai_types.Candidate | None, *, emitted_tool_calls: int
) -> StopReason | None:
    if candidate is None or candidate.finish_reason is None:
        return None
    if emitted_tool_calls:
        return StopReason.TOOL_USE
    return _FINISH_REASON_MAP.get(candidate.finish_reason, StopReason.END_TURN)


_ChunkEvent = (
    TextDeltaEvent | ThinkingDeltaEvent | ToolCallDeltaEvent | ToolCallEvent | WebSearchResultEvent
)


def _process_chunk(
    chunk: genai_types.GenerateContentResponse, emitted_tool_calls: int
) -> tuple[list[_ChunkEvent], int, StopReason | None, TokenUsage | None]:
    """One stream chunk's events + updated running state — pulled out of `stream()` purely to
    keep that method's own branching shallow."""
    candidate = chunk.candidates[0] if chunk.candidates else None
    parts = candidate.content.parts if candidate and candidate.content else None
    part_events, emitted_tool_calls = _events_from_chunk_parts(parts or [], emitted_tool_calls)
    events: list[_ChunkEvent] = list(part_events)
    grounding = candidate.grounding_metadata if candidate else None
    web_search_event = _web_search_result_event(grounding)
    if web_search_event is not None:
        events.append(web_search_event)
    stop_reason = _chunk_stop_reason(candidate, emitted_tool_calls=emitted_tool_calls)
    usage = _usage_from_metadata(chunk.usage_metadata) if chunk.usage_metadata is not None else None
    return events, emitted_tool_calls, stop_reason, usage


class GeminiClient:
    """The Google Gemini `LLMClient`."""

    def __init__(
        self,
        *,
        model_id: str,
        api_key: str,
        capabilities: ModelCapabilities | None = None,
        max_output_tokens: int = 4096,
    ) -> None:
        self.provider_id = "gemini"
        self.model_id = model_id
        # Both catalog models (`gemini-3.1-pro-preview`, `gemini-3.5-flash`) support Gemini's
        # native `google_search` grounding tool, and that grounding counts as native web search,
        # so `native_web_search=True` is accurate for this provider's
        # current model family, unlike OpenAI's Chat Completions catalog (see openai_client.py).
        self.capabilities = capabilities or ModelCapabilities(
            native_tool_calling=True,
            streaming=True,
            native_web_search=True,
            extended_thinking=_supports_extended_thinking(model_id),
            # Both catalog models (`gemini-3.1-pro-preview`, `gemini-3.5-flash`) are natively
            # multimodal per Google's own Gemini model docs — verified against the published
            # capabilities table, not assumed.
            supports_vision=True,
        )
        self._max_output_tokens = max_output_tokens
        # SDK client construction is local — no network call.
        self._client = genai.Client(api_key=api_key)

    def _config(
        self,
        system: str | None,
        tools: list[ToolSpec] | None,
        *,
        native_web_search: bool,
    ) -> genai_types.GenerateContentConfig:
        """Gemini currently disallows mixing function-calling tools with the `google_search`
        grounding tool in one request (a `Tool` with both `function_declarations` and
        `google_search` set is rejected) — verified against the installed `google-genai` SDK's
        `Tool` model, which carries both fields but the API rejects them combined. When this
        turn wants native web search, `google_search` wins and any function-declaration tools
        for the turn are skipped rather than raising: `web_search`/`fetch_url`'s Brave-backed
        equivalents are deliberately withheld by the orchestrator's `_maybe_add_web_search`
        branch in this same case (native capability replaces them, never both), so no
        turn-specific tool this client is asked to send in that state is actually load-bearing;
        preserving correctness (a valid request that gets grounding) matters more here than
        silently dropping to un-grounded function-calling tools instead."""
        config = genai_types.GenerateContentConfig(
            system_instruction=system, max_output_tokens=self._max_output_tokens
        )
        if self.capabilities.extended_thinking:
            # `thinking_level` left unset (`None`) — verified against the SDK's own field default
            # (`None`, no forced level), so the model's own default budget applies rather than
            # this client inventing a level with no documented recommended value.
            config.thinking_config = genai_types.ThinkingConfig(include_thoughts=True)
        if native_web_search:
            config.tools = [genai_types.Tool(google_search=genai_types.GoogleSearch())]
        elif tools:
            declarations = [_tool_spec_to_declaration(t) for t in tools]
            config.tools = [genai_types.Tool(function_declarations=declarations)]
        return config

    def _contents(self, messages: list[Message]) -> genai_types.ContentListUnion:
        # `list[Content]` is a `ContentListUnion` at runtime, but mypy's list invariance rejects
        # it as an argument to the SDK's broader `list[Content | str | Image | File | Part]`
        # member of that union — cast once here instead of at every call site.
        name_map = _build_tool_call_name_map(messages)
        contents = [_message_to_content(m, name_map) for m in messages]
        return cast("genai_types.ContentListUnion", contents)

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        system, turns = _split_system(messages)
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model_id,
                contents=self._contents(turns),
                config=self._config(system, tools, native_web_search=native_web_search),
            )
        except Exception as exc:
            # Never log the api_key (governance §6) — the SDK never surfaces it in exc text.
            logger.warning("llm_gemini_chat_failed", model=self.model_id, error=type(exc).__name__)
            raise

        candidate = response.candidates[0] if response.candidates else None
        parts = candidate.content.parts if candidate and candidate.content else None
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for index, part in enumerate(parts or []):
            if part.text:
                text_parts.append(part.text)
            elif part.function_call is not None:
                tool_calls.append(_function_call_to_tool_call(part.function_call, index))

        finish_reason = candidate.finish_reason if candidate else None
        stop_reason = StopReason.END_TURN
        if tool_calls:
            stop_reason = StopReason.TOOL_USE
        elif finish_reason is not None:
            stop_reason = _FINISH_REASON_MAP.get(finish_reason, StopReason.END_TURN)

        return LLMResponse(
            text="".join(text_parts) or None,
            tool_calls=tuple(tool_calls),
            stop_reason=stop_reason,
            usage=_usage_from_metadata(response.usage_metadata),
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
        system, turns = _split_system(messages)

        stop_reason = StopReason.END_TURN
        usage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
        # Gemini's `generate_content_stream` yields each function call whole (no incremental
        # JSON-arguments streaming for the catalog's default, non-AFC-stream config) — so tool
        # calls surface as one `ToolCallDeltaEvent` + immediate `ToolCallEvent`, never assembled
        # incrementally like Anthropic/OpenAI's `input_json_delta`/`function.arguments` chunks.
        emitted_tool_calls = 0

        stream = await self._client.aio.models.generate_content_stream(
            model=self.model_id,
            contents=self._contents(turns),
            config=self._config(system, tools, native_web_search=native_web_search),
        )
        async for chunk in stream:
            events, emitted_tool_calls, chunk_stop_reason, chunk_usage = _process_chunk(
                chunk, emitted_tool_calls
            )
            for event in events:
                yield event
            if chunk_stop_reason is not None:
                stop_reason = chunk_stop_reason
            if chunk_usage is not None:
                usage = chunk_usage

        yield StreamDoneEvent(stop_reason=stop_reason, usage=usage)

    async def count_tokens(self, messages: list[Message]) -> int:
        """Uses Gemini's real `count_tokens` endpoint — the SDK exposes it, so we use it for
        real counting rather than an estimate."""
        system, turns = _split_system(messages)
        result = await self._client.aio.models.count_tokens(
            model=self.model_id,
            contents=self._contents(turns),
            config=genai_types.CountTokensConfig(system_instruction=system) if system else None,
        )
        return result.total_tokens or 0


__all__ = ["GeminiClient"]
