"""Usage & cost accounting: the context-composition token breakdown that makes compaction /
branch-summary / deferred-tool-schema savings measurable, plus a running per-turn accumulator
that emits a live `usage` event on every update.

Scope judgment call: durable cross-turn rollups (`usage_events`/`usage_counters` — the
tier-quota rate-limit store) need a resolved per-user tier (`accounts/entitlements`, not built
yet) and are explicitly flagged as a concurrency-profile ("Postgres+Redis race/lost-update")
item, not a plain-SQLite one. Building that sink properly is a Phase-9/12 dependency, not this
phase's. What this phase delivers instead: the typed breakdown model, an in-process accumulator
a turn's hooks feed as the Step loop runs, and its projection onto the event bus (satisfying
the "measurable, live-emitted" requirement) — mirroring the same "structured in-process record,
not yet a DB sink" precedent `agent/journal.py` set for the tool-call audit trail. A future
sink writes `UsageAccumulator.snapshot()` into `usage_events` with no call-site change.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from events.bus import EventBus


class TokenBreakdown(BaseModel):
    """Input tokens attributed to each context-composition source for one turn. Every field
    defaults to 0 — a source that contributed nothing (e.g. no branch summary was needed) is
    recorded as zero, never omitted, so a regression (a summary silently ballooning, a
    deferred-tool set stopping being deferred) is visible in the shape itself."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    system_prompt: int = Field(default=0, ge=0)
    recent_messages: int = Field(default=0, ge=0)
    compaction_summary: int = Field(default=0, ge=0)
    branch_summaries: int = Field(default=0, ge=0)
    memory_block: int = Field(default=0, ge=0)
    tool_schemas_immediate: int = Field(default=0, ge=0)
    tool_schemas_deferred: int = Field(default=0, ge=0)
    tool_results: int = Field(default=0, ge=0)

    def total(self) -> int:
        return sum(self.model_dump().values())


class TurnUsage(BaseModel):
    """The per-turn accounting snapshot: totals plus the breakdown that explains them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn_id: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    finmind_calls: int = Field(default=0, ge=0)
    cost_micros: int = Field(default=0, ge=0)
    breakdown: TokenBreakdown = Field(default_factory=TokenBreakdown)


class UsageAccumulator:
    """Mutable per-turn accumulator. Not itself persisted — a caller (the turn-lifecycle
    `post_turn` hook) reads `.snapshot()` once the turn finishes and hands it to whatever
    sink is wired (event bus today; a future DB sink reads the same snapshot)."""

    def __init__(self, turn_id: str) -> None:
        self._turn_id = turn_id
        self._input_tokens = 0
        self._output_tokens = 0
        self._tool_calls = 0
        self._finmind_calls = 0
        self._cost_micros = 0
        self._breakdown = TokenBreakdown()

    def add_llm_call(self, *, input_tokens: int, output_tokens: int, cost_micros: int = 0) -> None:
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        self._cost_micros += cost_micros

    def add_tool_call(self, *, is_finmind: bool = False) -> None:
        self._tool_calls += 1
        if is_finmind:
            self._finmind_calls += 1

    def set_breakdown(self, breakdown: TokenBreakdown) -> None:
        self._breakdown = breakdown

    def snapshot(self) -> TurnUsage:
        return TurnUsage(
            turn_id=self._turn_id,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            tool_calls=self._tool_calls,
            finmind_calls=self._finmind_calls,
            cost_micros=self._cost_micros,
            breakdown=self._breakdown,
        )

    def publish(self, bus: EventBus, *, agent_id: str = "root") -> None:
        """Emit the current snapshot as a live `usage` event."""
        usage = self.snapshot()
        bus.publish(
            "usage",
            turn_id=self._turn_id,
            agent_id=agent_id,
            data=usage.model_dump(mode="json"),
        )


__all__ = ["TokenBreakdown", "TurnUsage", "UsageAccumulator"]
