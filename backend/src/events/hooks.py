"""Concrete handlers registered into `agent/`'s dispatch points from the outside — the
dependency-inversion half of the story described in `agent/hooks.py`. `agent/loop.py` only
knows `PostToolHook`/`PreLLMHook`/`CancelToken` as Protocol shapes; the handlers below are
built here (interaction layer) and handed into `AgentLoop`'s constructor at application-wiring
time (e.g. `api/` composing a request-scoped `AgentLoop`, itself out of scope for this phase).

Five handlers ship today:
- `EventBusPreToolHook` — publishes a `tool.start`-shaped internal event the instant a tool
  call is about to dispatch, so the frontend's process timeline has *something* to show live
  even when the model streams no `thinking_delta` at all (most configured models don't) —
  without this, a slow multi-tool Step showed nothing until every call in it had already
  finished and posted its `tool.result`.
- `EventBusPostToolHook` — publishes a `tool.result`-shaped internal event for every dispatch
  outcome (blocked / masked / real), so the SSE bridge and any metrics subscriber see it live.
- `EventBusThinkingHook` — publishes a `thinking.delta`-shaped internal event for every
  extended-thinking chunk the model streams, so the SSE bridge forwards it live to the
  frontend's timeline UI.
- `EventBusAnswerDeltaHook` — publishes a `message.delta`-shaped internal event for every
  already-governance-scanned sentence of the streaming final answer, so the frontend can
  render it token-by-token as it's generated instead of waiting for the single terminal
  `message` frame — closes the "answer looks frozen while the model drafts" gap that persisted
  even after `tool.start` shipped, since most turns' final Step has no tool calls left to show
  and (for most models) no `thinking_delta` either.
- `MemoryRecallPreLLMHook` — the `pre_llm` memory-injection handler; wraps `memory.recall.
  recall` and appends its rendered block as a system message right after compaction, never
  touching the governance-owned system prefix.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from events.bus import EventBus
from events.kinds import EventKind
from llm.base import Message, MessageRole, WebSearchSource


def _natural_language_summary(tool_name: str, *, guardrail_allowed: bool) -> str:
    """A short, frontend-facing narration of one tool dispatch — deliberately NOT
    `agent.loop._summarize_result`'s `TypeName(field, field)` journal/LLM-facing dump, which is
    an internal debugging shape a user-facing tool-call pill must never render verbatim."""
    label = tool_name.replace("_", " ")
    return f"Ran {label}" if guardrail_allowed else f"Blocked {label}"


class EventBusPreToolHook:
    """`pre_tool` handler: publishes a `tool.start` event the moment a call is about to
    dispatch — fires even for a call that governance goes on to block, since the frontend
    still wants to show "the agent tried to look this up" rather than nothing. Carries only
    the tool name (a human label, via the same `_natural_language_summary` helper
    `EventBusPostToolHook` uses) — no arguments, no result; those still arrive later on the
    matching `tool.result` frame this event is a live preview of."""

    def __init__(self, bus: EventBus, *, turn_id: str, agent_id: str = "root") -> None:
        self._bus = bus
        self._turn_id = turn_id
        self._agent_id = agent_id

    async def __call__(self, *, step_index: int, tool_name: str) -> None:
        self._bus.publish(
            EventKind.TOOL_START,
            turn_id=self._turn_id,
            agent_id=self._agent_id,
            data={
                "step_index": step_index,
                "tool_name": tool_name,
                "summary": _natural_language_summary(tool_name, guardrail_allowed=True),
            },
        )


class EventBusPostToolHook:
    """`post_tool` handler: republishes every tool-dispatch outcome onto the event bus as an
    internal `tool.result` event. Carries no raw payload — the `summary` field is a short
    natural-language narration derived from the tool name, not the raw `result_summary` the
    journal uses internally (that shape is debugging-oriented, not user-facing)."""

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
        del result_summary  # journal/LLM-facing shape — never the frontend's own `summary`
        self._bus.publish(
            EventKind.TOOL_RESULT,
            turn_id=self._turn_id,
            agent_id=self._agent_id,
            data={
                "step_index": step_index,
                "tool_name": tool_name,
                "argument_keys": list(argument_keys),
                "summary": _natural_language_summary(
                    tool_name, guardrail_allowed=guardrail_allowed
                ),
                "guardrail_allowed": guardrail_allowed,
                "guardrail_reason": guardrail_reason,
            },
        )


class EventBusThinkingHook:
    """`thinking` handler: republishes every extended-thinking delta chunk onto the event bus
    as a `thinking.delta` event, `{agent_id, text}` — `text` is the raw delta chunk (not
    accumulated), the frontend's `turn-store.ts` owns per-`agent_id` accumulation client-side,
    mirroring how `EventBusPostToolHook` republishes rather than accumulates."""

    def __init__(self, bus: EventBus, *, turn_id: str, agent_id: str = "root") -> None:
        self._bus = bus
        self._turn_id = turn_id
        self._agent_id = agent_id

    async def __call__(self, *, text: str) -> None:
        self._bus.publish(
            EventKind.THINKING_DELTA,
            turn_id=self._turn_id,
            agent_id=self._agent_id,
            data={"agent_id": self._agent_id, "text": text},
        )


class EventBusAnswerDeltaHook:
    """`answer_delta` handler: republishes every already-governance-scanned sentence of the
    streaming final answer onto the event bus as a `message.delta` event, `{agent_id, text}` —
    mirrors `EventBusThinkingHook`'s republish-verbatim shape. Only the root/synthesis answer
    should ever wire this (a `agent_id="root"` default, like every other hook here) — fan-out
    sub-agent lanes have no "final answer" of their own that reaches the user directly, so
    `_build_loop` never attaches this hook to a SUBAGENT-role loop (mirrors the `thinking`/
    `web_search` hook carve-out already documented on `orchestrator.py`'s fan-out paths)."""

    def __init__(self, bus: EventBus, *, turn_id: str, agent_id: str = "root") -> None:
        self._bus = bus
        self._turn_id = turn_id
        self._agent_id = agent_id

    async def __call__(self, *, text: str) -> None:
        self._bus.publish(
            EventKind.MESSAGE_DELTA,
            turn_id=self._turn_id,
            agent_id=self._agent_id,
            data={"agent_id": self._agent_id, "text": text},
        )


class EventBusWebSearchHook:
    """`web_search` handler: republishes every completed native-web-search call onto the event
    bus as a `web_search.result` event, `{agent_id, sources}` — `sources` is the full source
    list for that one search (already-complete, not a delta to accumulate), mirroring how
    `EventBusThinkingHook` republishes verbatim rather than transforming."""

    def __init__(self, bus: EventBus, *, turn_id: str, agent_id: str = "root") -> None:
        self._bus = bus
        self._turn_id = turn_id
        self._agent_id = agent_id

    async def __call__(self, *, sources: tuple[WebSearchSource, ...]) -> None:
        self._bus.publish(
            EventKind.WEB_SEARCH_RESULT,
            turn_id=self._turn_id,
            agent_id=self._agent_id,
            data={
                "agent_id": self._agent_id,
                "sources": [{"url": source.url, "title": source.title} for source in sources],
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


__all__ = [
    "EventBusAnswerDeltaHook",
    "EventBusPostToolHook",
    "EventBusPreToolHook",
    "EventBusThinkingHook",
    "EventBusWebSearchHook",
    "MemoryRecallPreLLMHook",
    "RecallBlockProvider",
]
