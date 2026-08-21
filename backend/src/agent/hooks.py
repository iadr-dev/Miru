"""Dependency-inverted extension Protocols for the Step-loop dispatch points that had no
registration seam before this phase: `pre_tool` (fires right before a tool call is dispatched,
observe-only — NOT the governance veto hook of the same informal name in `agent.guardrails`,
see `PreToolHook`'s own docstring), `post_tool` (fires after every tool dispatch, cannot
veto), `pre_llm` (runs on the message list right before every model call, cannot veto —
the only pre_llm check that can veto, the token-budget/compaction trigger, stays governance-
owned and hardcoded in `agent.context_window.compact`), `thinking` (fires once per
extended-thinking delta chunk during a streamed Step, also observe-only), `answer_delta`
(fires once per already-governance-scanned sentence of the final answer as it streams, also
observe-only), and `web_search` (fires once per completed native-web-search call during a
streamed Step, also observe-only).

`agent/loop.py` only ever imports the Protocol shapes below — never a concrete handler. Higher
layers (the interaction-layer `events/` package) build concrete handlers (journal writers,
event-bus publishers, memory-block injectors) and pass them into `AgentLoop`'s constructor at
whatever call site wires the application together. That call site lives above `agent/`, so
`agent/` never imports `events/`, `memory/`, or any interaction-layer package — the one-way
dependency rule holds. This is the same pattern already used for `pre_tool`/`pre_output`
(`agent.guardrails`), except those two are the *mandatory, non-removable* governance hooks and
stay hardcoded call sites in `loop.py`, not registrable slots — only the non-veto,
side-effect-only points below are opened up for external registration.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from llm.base import Message, WebSearchSource


@runtime_checkable
class PreToolHook(Protocol):
    """Fires the moment a tool call is ABOUT to be dispatched — before the governance
    `check_tool_call` veto even runs, so it fires for a call that ends up blocked too. This is
    the "the agent is now looking something up" signal a live UI needs regardless of whether
    the model happens to stream genuine reasoning tokens (`ThinkingHook`) for this turn: most
    configured models never emit a `thinking_delta` at all, so `PostToolHook`'s after-the-fact
    `tool.result` was previously the ONLY per-tool signal — meaning a slow multi-tool Step showed
    nothing to the user until every call in it had already finished. Never vetoes; a `pre_tool`
    handler observes, it does not gate (the actual veto is `agent.guardrails.check_tool_call`,
    called separately and first)."""

    async def __call__(self, *, step_index: int, tool_name: str) -> None: ...


@runtime_checkable
class PostToolHook(Protocol):
    """Fires after every tool dispatch (blocked, masked, or real). Never vetoes — a `post_tool`
    handler observes, it does not gate."""

    async def __call__(  # noqa: PLR0913 — one typed field per journaled dimension, matching
        # `agent.journal.Journal.record_tool_call`'s own argument shape
        self,
        *,
        step_index: int,
        tool_name: str,
        argument_keys: tuple[str, ...],
        result_summary: str,
        guardrail_allowed: bool,
        guardrail_reason: str | None,
    ) -> None: ...


@runtime_checkable
class PreLLMHook(Protocol):
    """Runs on the message list right before each `llm.chat` call, after compaction. May
    append/transform messages (e.g. inject a recalled-memory block) but never removes the
    governance-owned system prefix or vetoes the call — the only vetoing pre_llm check
    (token-budget) is `agent.context_window.compact`, which runs first and is not
    registrable."""

    async def __call__(self, *, messages: list[Message]) -> list[Message]: ...


@runtime_checkable
class ThinkingHook(Protocol):
    """Fires once per extended-thinking delta chunk the model streams (`llm.base.
    ThinkingDeltaEvent`), already governance-scanned by the loop before this hook ever sees it
    (see `AgentLoop._stream_step`) — mirrors `PostToolHook`'s "observe, never veto" contract.
    Registered the same way as `PostToolHook`/`PreLLMHook`: a concrete handler (the
    interaction-layer `events.hooks.EventBusThinkingHook`) is built above `agent/` and passed
    into `AgentLoop`'s constructor, so this module only ever depends on the Protocol shape."""

    async def __call__(self, *, text: str) -> None: ...


@runtime_checkable
class AnswerDeltaHook(Protocol):
    """Fires once per completed, already-governance-scanned sentence of the FINAL answer as it
    streams (`AgentLoop._stream_final_answer`) — the live token-by-token render seam, mirroring
    `ThinkingHook`'s contract but for the answer text itself rather than reasoning. Unlike
    `ThinkingHook` (which forwards a raw model-generated chunk as soon as it arrives), each
    chunk here is a WHOLE sentence, and it has already passed `agent.guardrails.scan_sentence`
    before this hook ever sees it — trading language and vendor names are stripped in place,
    so what streams is always safe to render immediately, never provisional text a later pass
    might still redact. This is what makes streaming compatible with the mandatory
    non-negotiable `pre_output` scan: the scan runs per-sentence as sentences close, not once
    on the whole buffered answer at the end (that final whole-answer `check_output` pass still
    runs too, for the caveat-append step — never removed, only additionally chunked)."""

    async def __call__(self, *, text: str) -> None: ...


@runtime_checkable
class WebSearchHook(Protocol):
    """Fires once per completed native-web-search call the model streams (`llm.base.
    WebSearchResultEvent`) — mirrors `ThinkingHook`'s "observe, never veto" contract. Unlike
    thinking text, sources are structured metadata (url/title), not free text, so this does
    not run through `guardrails.check_output` — there is no prose to scan, and the URLs/titles
    are exactly what the provider's own web-search call already surfaced, no different in kind
    from a tool-result summary a `PostToolHook` already observes unscanned."""

    async def __call__(self, *, sources: tuple[WebSearchSource, ...]) -> None: ...


@runtime_checkable
class CancelToken(Protocol):
    """A cooperative cancellation flag `AgentLoop.run_turn` polls between Steps. Read-only from
    the loop's perspective — whoever holds the token (the interaction-layer turn lifecycle)
    flips it in response to a client cancel request; in-flight tool calls for the current Step
    are still allowed to finish (read-only, safe), only the next Step is refused."""

    def is_cancelled(self) -> bool: ...


@runtime_checkable
class SteeringSource(Protocol):
    """The mid-run steering-message reach-in seam: whoever holds the live turn's steering queue
    (the interaction-layer turn registry) hands `AgentLoop.run_turn` a `SteeringSource`, polled
    at each Step boundary (never mid-Step, mirroring `CancelToken`'s own boundary discipline).

    `drain()` both reads and clears the pending queue in one call — the loop must never re-see
    a message it already injected, and a message that arrives after the last Step (the turn is
    about to finalize) is simply never drained, not lost from the caller's perspective (the
    caller's own `steering` list is what `POST .../steer` appended to; draining here only
    consumes the copy the loop folds into context, and the accepted-echo the endpoint already
    publishes is unaffected)."""

    def drain(self) -> tuple[str, ...]: ...


__all__ = [
    "AnswerDeltaHook",
    "CancelToken",
    "PostToolHook",
    "PreLLMHook",
    "PreToolHook",
    "SteeringSource",
    "ThinkingHook",
    "WebSearchHook",
]
