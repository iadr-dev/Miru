"""Dependency-inverted extension Protocols for the two Step-loop dispatch points that had no
registration seam before this phase: `post_tool` (fires after every tool dispatch, cannot
veto) and `pre_llm` (runs on the message list right before every model call, cannot veto —
the only pre_llm check that can veto, the token-budget/compaction trigger, stays governance-
owned and hardcoded in `agent.context_window.compact`).

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

from llm.base import Message


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


__all__ = ["CancelToken", "PostToolHook", "PreLLMHook", "SteeringSource"]
