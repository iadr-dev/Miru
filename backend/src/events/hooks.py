"""Concrete handlers registered into `agent/`'s dispatch points from the outside — the
dependency-inversion half of the story described in `agent/hooks.py`. `agent/loop.py` only
knows `PostToolHook`/`PreLLMHook`/`CancelToken` as Protocol shapes; the handlers below are
built here (interaction layer) and handed into `AgentLoop`'s constructor at application-wiring
time (e.g. `api/` composing a request-scoped `AgentLoop`, itself out of scope for this phase).

Two handlers ship today:
- `EventBusPostToolHook` — publishes a `tool.result`-shaped internal event for every dispatch
  outcome (blocked / masked / real), so the SSE bridge and any metrics subscriber see it live.
- `MemoryRecallPreLLMHook` — the `pre_llm` memory-injection handler; wraps `memory.recall.
  recall` and appends its rendered block as a system message right after compaction, never
  touching the governance-owned system prefix.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from events.bus import EventBus
from llm.base import Message, MessageRole


class EventBusPostToolHook:
    """`post_tool` handler: republishes every tool-dispatch outcome onto the event bus as an
    internal `tool.result` event. Carries no raw payload — only the same redacted
    `result_summary` the journal already uses."""

    def __init__(self, bus: EventBus, *, turn_id: str, agent_id: str = "root") -> None:
        self._bus = bus
        self._turn_id = turn_id
        self._agent_id = agent_id

    async def __call__(  # noqa: PLR0913 — mirrors PostToolHook's own shape
        self,
        *,
        step_index: int,
        tool_name: str,
        argument_keys: tuple[str, ...],
        result_summary: str,
        guardrail_allowed: bool,
        guardrail_reason: str | None,
    ) -> None:
        self._bus.publish(
            "tool.result",
            turn_id=self._turn_id,
            agent_id=self._agent_id,
            data={
                "step_index": step_index,
                "tool_name": tool_name,
                "argument_keys": list(argument_keys),
                "summary": result_summary,
                "guardrail_allowed": guardrail_allowed,
                "guardrail_reason": guardrail_reason,
            },
        )


RecallBlockProvider = Callable[[], Awaitable[str | None]]


class MemoryRecallPreLLMHook:
    """`pre_llm` handler: injects the compact recalled-memory block (rendered by
    `memory.recall`) as a system message, once per turn. `provider` is a zero-argument
    thunk (already bound to the thread/query it needs) rather than this hook importing
    `memory/` types directly into its signature — keeps this class swappable/testable without
    a live `MemoryStore`."""

    def __init__(self, provider: RecallBlockProvider) -> None:
        self._provider = provider
        self._injected = False

    async def __call__(self, *, messages: list[Message]) -> list[Message]:
        if self._injected:
            return messages
        block = await self._provider()
        self._injected = True
        if not block:
            return messages
        memory_message = Message(role=MessageRole.SYSTEM, content=block)
        if messages and messages[0].role is MessageRole.SYSTEM:
            return [messages[0], memory_message, *messages[1:]]
        return [memory_message, *messages]


__all__ = ["EventBusPostToolHook", "MemoryRecallPreLLMHook", "RecallBlockProvider"]
