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
from events.kinds import EventKind
from llm.pricing import prompt_tokens


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


class ModelUsage(BaseModel):
    """One turn's LLM consumption on a single (provider, model) - what the usage settings chart
    is built from. `cost_micros` is only filled where the provider reports a charge inline
    (OpenRouter); it is 0, not an estimate, everywhere else."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cost_micros: int = Field(default=0, ge=0)


class MemoryRef(BaseModel):
    """A long-term memory that informed this turn - shown to the user under the answer. `text` is
    a short snippet (the memory is the user's own), enough to recognise it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    key: str
    type: str
    text: str = ""


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
    models: tuple[ModelUsage, ...] = Field(default_factory=tuple)
    # The memories recall put in front of the model this turn (empty when none or memory is off).
    memories_used: tuple[MemoryRef, ...] = Field(default_factory=tuple)
    # The 語氣 preset (id) that shaped this answer, so a reopened thread can still say which.
    personality: str | None = None


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
        self._models: dict[tuple[str, str], ModelUsage] = {}

    def add_llm_call(  # noqa: PLR0913 - one field per usage dimension a provider reports
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_micros: int = 0,
        provider: str = "",
        model: str = "",
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> None:
        # The turn total is what is shown to the user: every token read, cache included.
        self._input_tokens += prompt_tokens(
            provider, input_tokens, cached_tokens, cache_write_tokens
        )
        self._output_tokens += output_tokens
        self._cost_micros += cost_micros
        if provider or model:
            key = (provider, model)
            seen = self._models.get(key) or ModelUsage(provider=provider, model=model)
            self._models[key] = ModelUsage(
                provider=provider,
                model=model,
                calls=seen.calls + 1,
                input_tokens=seen.input_tokens + input_tokens,
                output_tokens=seen.output_tokens + output_tokens,
                cached_tokens=seen.cached_tokens + cached_tokens,
                cache_write_tokens=seen.cache_write_tokens + cache_write_tokens,
                reasoning_tokens=seen.reasoning_tokens + reasoning_tokens,
                cost_micros=seen.cost_micros + cost_micros,
            )

    def add_tool_call(self, *, is_finmind: bool = False) -> None:
        self._tool_calls += 1
        if is_finmind:
            self._finmind_calls += 1

    def snapshot(self) -> TurnUsage:
        return TurnUsage(
            turn_id=self._turn_id,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            tool_calls=self._tool_calls,
            finmind_calls=self._finmind_calls,
            cost_micros=self._cost_micros,
            breakdown=self._breakdown,
            models=tuple(self._models.values()),
        )

    def publish(self, bus: EventBus, *, agent_id: str = "root") -> None:
        """Emit the current snapshot as a live `usage` event."""
        usage = self.snapshot()
        bus.publish(
            EventKind.USAGE,
            turn_id=self._turn_id,
            agent_id=agent_id,
            data=usage.model_dump(mode="json"),
        )


__all__ = ["ModelUsage", "TokenBreakdown", "TurnUsage", "UsageAccumulator"]
