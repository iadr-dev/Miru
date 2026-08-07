"""Anthropic Claude client — the **default**, reference-implementation `LLMClient`.
Wraps the official `anthropic` SDK.

The SDK's own HTTP transport is synchronous under the hood for some calls but we use its
`AsyncAnthropic` client throughout, so no `anyio.to_thread.run_sync` wrapping is needed here
(unlike yfinance in Phase 2) — the SDK is natively async end-to-end.

Constructing this client never touches the network (SDK client construction is local); only
`chat()`/`stream()`/`count_tokens()` call the Anthropic API.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Final, cast

import structlog
from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage
from anthropic.types import MessageParam

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
    ToolResultPart,
    ToolSpec,
)

logger = structlog.get_logger(__name__)

_STOP_REASON_MAP: Final[dict[str, StopReason]] = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "stop_sequence": StopReason.END_TURN,
}


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
    return cast("MessageParam", {"role": role, "content": message.content or ""})


def _tool_spec_to_param(spec: ToolSpec) -> dict[str, Any]:
    return {"name": spec.name, "description": spec.description, "input_schema": spec.parameters}


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


def _content_block_event(
    event: Any, pending: dict[int, dict[str, str]]
) -> TextDeltaEvent | ToolCallDeltaEvent | None:
    """Extract a text/tool-call delta from one raw stream event, if any (helper split out of
    `AnthropicClient.stream` to keep branch count under the ruff PLR0912 limit)."""
    if event.type == "content_block_start":
        block = event.content_block
        if block.type == "tool_use":
            pending[event.index] = {"id": block.id, "name": block.name, "args": ""}
            return ToolCallDeltaEvent(id=block.id, name=block.name)
        return None
    if event.type == "content_block_delta":
        delta = event.delta
        if delta.type == "text_delta":
            return TextDeltaEvent(text=delta.text)
        if delta.type == "input_json_delta":
            slot = pending.get(event.index)
            if slot is not None:
                slot["args"] += delta.partial_json
                return ToolCallDeltaEvent(id=slot["id"], arguments_delta=delta.partial_json)
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
        )
    if event.type == "message_start":
        return stop_reason, TokenUsage(
            input_tokens=event.message.usage.input_tokens,
            output_tokens=0,
            total_tokens=event.message.usage.input_tokens,
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
    ) -> None:
        self.provider_id = "anthropic"
        self.model_id = model_id
        self.capabilities = capabilities or ModelCapabilities(
            native_tool_calling=True, streaming=True, native_web_search=True
        )
        self._max_tokens = max_tokens
        # SDK client construction is local — no network call.
        self._client = AsyncAnthropic(api_key=api_key)

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        system, turns = _system_and_turns(messages)
        params = [_message_to_param(m) for m in turns]
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self._max_tokens,
            "messages": params,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [_tool_spec_to_param(t) for t in tools]

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
        )
        return LLMResponse(
            text="".join(text_parts) or None,
            tool_calls=tuple(tool_calls),
            stop_reason=stop_reason,
            usage=usage,
        )

    def _stream_kwargs(
        self, messages: list[Message], tools: list[ToolSpec] | None
    ) -> dict[str, Any]:
        system, turns = _system_and_turns(messages)
        params = [_message_to_param(m) for m in turns]
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self._max_tokens,
            "messages": params,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [_tool_spec_to_param(t) for t in tools]
        return kwargs

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[TextDeltaEvent | ToolCallDeltaEvent | ToolCallEvent | StreamDoneEvent]:
        kwargs = self._stream_kwargs(messages, tools)

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
                    id=slot["id"], name=slot["name"], arguments=_parse_json_object(slot["args"])
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
