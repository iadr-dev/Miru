"""Anthropic Claude client — the **default**, reference-implementation `LLMClient`.
Wraps the official `anthropic` SDK.

The SDK's own HTTP transport is synchronous under the hood for some calls but we use its
`AsyncAnthropic` client throughout, so no `anyio.to_thread.run_sync` wrapping is needed here
(unlike yfinance in Phase 2) — the SDK is natively async end-to-end.

Constructing this client never touches the network (SDK client construction is local); only
`chat()`/`stream()`/`count_tokens()` call the Anthropic API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Final, cast

import structlog
from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage
from anthropic.types import MessageParam, ThinkingConfigParam, WebSearchToolResultBlock

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
from llm.shared import parse_json_object

logger = structlog.get_logger(__name__)

_STOP_REASON_MAP: Final[dict[str, StopReason]] = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "stop_sequence": StopReason.END_TURN,
}

# Anthropic's native server-side web-search tool — a raw block appended to `tools` alongside
# the `_tool_spec_to_param`-built function-tool entries, never converted from a `ToolSpec` (it
# has no JSON-schema `input_schema`; Claude drives it itself). Verified against the installed
# `anthropic` SDK's `WebSearchTool20250305Param` (`anthropic.types`) — `name`/`type` are the
# only required fields.
_WEB_SEARCH_TOOL_BLOCK: Final[dict[str, Any]] = {
    "type": "web_search_20250305",
    "name": "web_search",
}

_CACHE_CONTROL_EPHEMERAL: Final[dict[str, str]] = {"type": "ephemeral"}

# Models with native extended-thinking support in this build — matched by prefix so a dated
# snapshot id under either family still qualifies. Verified against the installed SDK's
# `ThinkingConfigAdaptiveParam` (adaptive thinking, no `budget_tokens`) — see `_thinking_param`.
# `claude-sonnet-4-6` (this deployment's own default/agent-role model, `llm/registry.py`'s
# `_DEFAULT_MODEL`) is on this list too — confirmed against Anthropic's own supported-model
# table (docs/build-with-claude/thinking-troubleshooting, "Configurations each model rejects"):
# its row reads "Adaptive, extended (deprecated)" / default "Off" / rejects nothing — adaptive
# thinking is accepted, just not sent unless a caller opts in, which is what omitting this
# model from the list was silently doing (no `thinking` param ever sent -> always Off, zero
# `thinking.delta` events even though the model supports genuinely streaming them).
_EXTENDED_THINKING_MODEL_PREFIXES: Final[tuple[str, ...]] = (
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
)


def _supports_extended_thinking(model_id: str) -> bool:
    return any(model_id.startswith(prefix) for prefix in _EXTENDED_THINKING_MODEL_PREFIXES)


def _system_and_turns(messages: list[Message]) -> tuple[str | None, list[Message]]:
    """Anthropic takes `system` as a top-level param, not a message in the list."""
    system_parts = [m.content for m in messages if m.role is MessageRole.SYSTEM and m.content]
    turns = [m for m in messages if m.role is not MessageRole.SYSTEM]
    system = "\n\n".join(system_parts) if system_parts else None
    return system, turns


def _tool_result_block(part: ToolResultPart) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": part.tool_call_id,
        "content": part.content,
        "is_error": part.is_error,
    }


def _image_block(image: ImagePart) -> dict[str, Any]:
    """One `image`-type content block — verified against the installed SDK's `ImageBlockParam`
    (`type`/`source`) + `Base64ImageSourceParam` (`type: "base64"`/`media_type`/`data`)."""
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": image.media_type, "data": image.data},
    }


def _message_to_param(message: Message) -> MessageParam:
    if message.role is MessageRole.TOOL:
        content = [_tool_result_block(part) for part in message.tool_results]
        return cast("MessageParam", {"role": "user", "content": content})
    if message.role is MessageRole.ASSISTANT and message.tool_calls:
        blocks: list[dict[str, Any]] = []
        if message.content:
            blocks.append({"type": "text", "text": message.content})
        blocks.extend(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
            for call in message.tool_calls
        )
        return cast("MessageParam", {"role": "assistant", "content": blocks})
    role = "assistant" if message.role is MessageRole.ASSISTANT else "user"
    if message.images:
        # Images-plus-text is a content-block list, per the SDK: image blocks then the text
        # block last — Anthropic's own examples order image(s) before the text referencing them.
        blocks = [_image_block(image) for image in message.images]
        if message.content:
            blocks.append({"type": "text", "text": message.content})
        return cast("MessageParam", {"role": role, "content": blocks})
    return cast("MessageParam", {"role": role, "content": message.content or ""})


def _tool_spec_to_param(spec: ToolSpec) -> dict[str, Any]:
    return {"name": spec.name, "description": spec.description, "input_schema": spec.parameters}


def _system_param(system: str) -> list[dict[str, Any]]:
    """Render the system prompt as a single cacheable content block — `cache_control` on a
    plain string `system` isn't possible (the field must be a content-block list to carry it),
    so this always takes the list form. Verified against the installed SDK's `TextBlockParam`
    (`text`/`type`/`cache_control`)."""
    return [{"type": "text", "text": system, "cache_control": _CACHE_CONTROL_EPHEMERAL}]


def _tools_param(
    tools: list[ToolSpec] | None, *, native_web_search: bool
) -> list[dict[str, Any]] | None:
    """Build the `tools` list, marking the last entry cacheable so the whole (static) schema
    block is cached across turns — cache_control on the last tool caches every tool before it
    too, mirroring the system-prompt breakpoint (see `shared/prompt-caching.md`'s placement
    guidance, applied here without a doc citation per this repo's comment convention)."""
    params = [_tool_spec_to_param(t) for t in tools] if tools else []
    if native_web_search:
        params.append(dict(_WEB_SEARCH_TOOL_BLOCK))
    if not params:
        return None
    params[-1] = {**params[-1], "cache_control": _CACHE_CONTROL_EPHEMERAL}
    return params


def _thinking_param() -> ThinkingConfigParam:
    """Adaptive-thinking config — verified against the installed SDK's
    `ThinkingConfigAdaptiveParam`, which carries no `budget_tokens` field. `claude-opus-4-8`/
    `claude-sonnet-5` reject the older `{"type": "enabled", "budget_tokens": N}` shape with a
    400 (adaptive-only per Anthropic's own supported-model table); `claude-sonnet-4-6` still
    accepts that legacy shape too but it's deprecated there in favor of adaptive — so every
    model `_EXTENDED_THINKING_MODEL_PREFIXES` flags gets the same adaptive param here, never
    the budget_tokens fallback the task brief allows for pre-4.7-style models — only called
    when `ModelCapabilities.extended_thinking` is `True` for exactly those models."""
    return cast("ThinkingConfigParam", {"type": "adaptive"})


def _web_search_result_event(
    block: WebSearchToolResultBlock,
) -> WebSearchResultEvent | None:
    """Build a `WebSearchResultEvent` from a `web_search_tool_result` content block, if it
    carries real sources — verified live: the block's `content` is either a
    `WebSearchToolResultError` (no sources, e.g. the query was blocked/rate-limited) or a list
    of `WebSearchResultBlock`s (each carrying real `url`/`title` values, confirmed present on
    every result in a live query), so a bare `isinstance(content, list)` check is all that's
    needed to distinguish the two — every item in that list is a `WebSearchResultBlock`, per
    the SDK's own `WebSearchToolResultBlockContent` union."""
    content = block.content
    if not isinstance(content, list):
        return None
    sources = tuple(WebSearchSource(url=result.url, title=result.title) for result in content)
    return WebSearchResultEvent(sources=sources) if sources else None


def _content_block_start_event(
    event: Any, pending: dict[int, dict[str, str]]
) -> ToolCallDeltaEvent | WebSearchResultEvent | None:
    """Extract a tool-call/web-search-result event from one `content_block_start` event, if
    any (split out of `_content_block_event` purely to keep that function's own branch count
    under the ruff PLR0912/PLR0911 limits).

    A `thinking` block (SDK type `ThinkingBlock`) carries nothing to track — unlike `tool_use`,
    there is no id/name to seed `pending` with, the thinking text only ever arrives via
    subsequent `content_block_delta` events — so it falls through to the `None` return below,
    verified against the installed `anthropic` SDK's `RawContentBlockStartEvent.content_block`
    union (`ThinkingBlock` has just `thinking`/`signature` fields, no id).

    A `web_search_tool_result` block (SDK type `WebSearchToolResultBlock`) arrives as one
    complete unit — verified live: the full list of `WebSearchResultBlock`s (each with
    `url`/`title` already populated) is present on the `content_block_start` event itself,
    nothing streams incrementally for it — so it is turned into a `WebSearchResultEvent` right
    here rather than assembled like a tool call."""
    block = event.content_block
    if block.type == "tool_use":
        pending[event.index] = {"id": block.id, "name": block.name, "args": ""}
        return ToolCallDeltaEvent(id=block.id, name=block.name)
    if block.type == "web_search_tool_result":
        return _web_search_result_event(block)
    return None


def _content_block_delta_event(
    event: Any, pending: dict[int, dict[str, str]]
) -> TextDeltaEvent | ThinkingDeltaEvent | ToolCallDeltaEvent | None:
    """Extract a text/thinking/tool-call delta from one `content_block_delta` event, if any
    (split out of `_content_block_event` for the same PLR0912/PLR0911 reason as
    `_content_block_start_event`).

    `ThinkingDelta` (verified: `delta.type == "thinking_delta"`, field `delta.thinking: str`)
    is the actual incremental reasoning text. `SignatureDelta` (the block's trailing signature,
    `delta.type == "signature_delta"`) carries no user-facing text and is intentionally not
    surfaced as an event. `CitationsDelta` (per-cited-text-span source attribution interleaved
    with the answer text) is also intentionally not surfaced as a separate event: it references
    the exact same url/title already captured from the `web_search_tool_result` block, just
    scoped to a smaller span of the answer, which this normalized shape (one source list per
    search) does not need."""
    delta = event.delta
    if delta.type == "text_delta":
        return TextDeltaEvent(text=delta.text)
    if delta.type == "thinking_delta":
        return ThinkingDeltaEvent(text=delta.thinking)
    if delta.type == "input_json_delta":
        slot = pending.get(event.index)
        if slot is not None:
            slot["args"] += delta.partial_json
            return ToolCallDeltaEvent(id=slot["id"], arguments_delta=delta.partial_json)
    return None


def _content_block_event(
    event: Any, pending: dict[int, dict[str, str]]
) -> TextDeltaEvent | ThinkingDeltaEvent | ToolCallDeltaEvent | WebSearchResultEvent | None:
    """Extract a text/thinking/tool-call/web-search-result event from one raw stream event, if
    any (helper split out of `AnthropicClient.stream` to keep branch count under the ruff
    PLR0912 limit) — dispatches to `_content_block_start_event`/`_content_block_delta_event`
    by the raw event's own `type`."""
    if event.type == "content_block_start":
        return _content_block_start_event(event, pending)
    if event.type == "content_block_delta":
        return _content_block_delta_event(event, pending)
    return None


def _update_message_state(
    event: Any, stop_reason: StopReason, usage: TokenUsage
) -> tuple[StopReason, TokenUsage]:
    """Fold `message_start`/`message_delta` events into the running stop-reason/usage state."""
    if event.type == "message_delta":
        new_stop_reason = stop_reason
        if event.delta.stop_reason:
            new_stop_reason = _STOP_REASON_MAP.get(event.delta.stop_reason, StopReason.END_TURN)
        return new_stop_reason, TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=event.usage.output_tokens,
            total_tokens=usage.input_tokens + event.usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            cache_write_tokens=usage.cache_write_tokens,
        )
    if event.type == "message_start":
        return stop_reason, TokenUsage(
            input_tokens=event.message.usage.input_tokens,
            output_tokens=0,
            total_tokens=event.message.usage.input_tokens,
            cached_tokens=event.message.usage.cache_read_input_tokens or 0,
            cache_write_tokens=event.message.usage.cache_creation_input_tokens or 0,
        )
    return stop_reason, usage


class AnthropicClient:
    """The Anthropic `LLMClient` — default provider."""

    def __init__(
        self,
        *,
        model_id: str,
        api_key: str,
        capabilities: ModelCapabilities | None = None,
        max_tokens: int = 4096,
        extended_thinking: bool | None = None,
    ) -> None:
        self.provider_id = "anthropic"
        self.model_id = model_id
        self.capabilities = capabilities or ModelCapabilities(
            native_tool_calling=True,
            streaming=True,
            native_web_search=True,
            extended_thinking=(
                extended_thinking
                if extended_thinking is not None
                else _supports_extended_thinking(model_id)
            ),
            # Every model in Anthropic's current catalog (Claude Opus/Sonnet/Haiku 4.x, 5.x)
            # supports image input per Anthropic's own vision documentation — verified against
            # the published model-capabilities table, not assumed; no model id in this build's
            # catalog is a text-only variant.
            supports_vision=True,
        )
        self._max_tokens = max_tokens
        # SDK client construction is local — no network call.
        self._client = AsyncAnthropic(api_key=api_key)

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        system, turns = _system_and_turns(messages)
        params = [_message_to_param(m) for m in turns]
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self._max_tokens,
            "messages": params,
        }
        if system:
            kwargs["system"] = _system_param(system)
        tools_param = _tools_param(tools, native_web_search=native_web_search)
        if tools_param is not None:
            kwargs["tools"] = tools_param
        if self.capabilities.extended_thinking:
            kwargs["thinking"] = _thinking_param()

        try:
            response = cast("AnthropicMessage", await self._client.messages.create(**kwargs))
        except Exception as exc:
            # Never log the api_key (governance §6) — the SDK never surfaces it in exc text.
            logger.warning(
                "llm_anthropic_chat_failed", model=self.model_id, error=type(exc).__name__
            )
            raise

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
        stop_reason = _STOP_REASON_MAP.get(response.stop_reason or "end_turn", StopReason.END_TURN)
        usage = TokenUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
            cached_tokens=response.usage.cache_read_input_tokens or 0,
            cache_write_tokens=response.usage.cache_creation_input_tokens or 0,
        )
        return LLMResponse(
            text="".join(text_parts) or None,
            tool_calls=tuple(tool_calls),
            stop_reason=stop_reason,
            usage=usage,
        )

    def _stream_kwargs(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        *,
        native_web_search: bool,
    ) -> dict[str, Any]:
        system, turns = _system_and_turns(messages)
        params = [_message_to_param(m) for m in turns]
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self._max_tokens,
            "messages": params,
        }
        if system:
            kwargs["system"] = _system_param(system)
        tools_param = _tools_param(tools, native_web_search=native_web_search)
        if tools_param is not None:
            kwargs["tools"] = tools_param
        if self.capabilities.extended_thinking:
            kwargs["thinking"] = _thinking_param()
        return kwargs

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
        kwargs = self._stream_kwargs(messages, tools, native_web_search=native_web_search)

        # Assembled per content-block index as Anthropic streams tool-call JSON input
        # incrementally via `input_json_delta` events keyed to the block's index.
        pending: dict[int, dict[str, str]] = {}
        stop_reason = StopReason.END_TURN
        usage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                content_event = _content_block_event(event, pending)
                if content_event is not None:
                    yield content_event
                stop_reason, usage = _update_message_state(event, stop_reason, usage)

        for slot in pending.values():
            yield ToolCallEvent(
                tool_call=ToolCall(
                    id=slot["id"], name=slot["name"], arguments=parse_json_object(slot["args"])
                )
            )
        yield StreamDoneEvent(stop_reason=stop_reason, usage=usage)

    async def count_tokens(self, messages: list[Message]) -> int:
        """Uses Anthropic's real token-counting endpoint (`messages.count_tokens`) — the
        documented, exact approach available for this provider."""
        system, turns = _system_and_turns(messages)
        params = [_message_to_param(m) for m in turns]
        kwargs: dict[str, Any] = {"model": self.model_id, "messages": params}
        if system:
            kwargs["system"] = system
        result = await self._client.messages.count_tokens(**kwargs)
        return result.input_tokens


__all__ = ["AnthropicClient"]
