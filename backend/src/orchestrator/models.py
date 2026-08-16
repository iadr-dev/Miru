"""Orchestrator contracts: the routing decision, the per-lane structured result (carrying a
completeness/confidence signal so synthesis can tell an empty/low-confidence lane from a good
one), the inter-agent `Finding` hand-off record for `team`, and the synthesized-answer envelope.

Every model here is a frozen Pydantic v2 shape — a routing decision, a lane outcome, and a
finding are all journaled/provenance artifacts, so they are immutable by construction, the
same convention `conversation.models`/`memory.models` follow.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.loop import RichCard, ToolCallRecord
from events.usage import TurnUsage
from llm.base import WebSearchSource

Framework = Literal["none", "single", "subagent", "team"]

# A rare-hallucination backstop on the routing decision itself, not the routine fan-out
# breadth control — that's `TurnBudget.max_subagents` (orchestrator/ceilings.py), which is the
# real cost/latency-bearing ceiling and stays at 4. This only guards against a classifier verdict
# naming an unreasonable number of skills.
_MAX_SKILLS = 8


class Routing(BaseModel):
    """The intent-classifier verdict: which framework runs the turn and which 0-8 skills feed
    it. `reason` is journaled with the `route.decided` event so a routing decision is always
    reconstructable. `requested_skill_count` is the classifier's pre-truncation skill count (how
    many known skills it actually named before the `_MAX_SKILLS` backstop applied) — carried
    separately from `skills` so a later clamp (this ceiling, or `TurnBudget.clamp_subagents`) can
    report an honest "N angles dropped" count instead of only seeing the already-truncated list."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    framework: Framework
    skills: tuple[str, ...] = Field(default_factory=tuple)
    reason: str = Field(min_length=1)
    requested_skill_count: int = 0

    def bounded_skills(self) -> tuple[str, ...]:
        """The skills list clamped to the hard ceiling — a classifier that names
        more is trimmed here, never allowed to fan out past the bound."""
        return self.skills[:_MAX_SKILLS]


LaneStatus = Literal["ok", "degraded", "dropped", "unavailable_tier", "error"]
Completeness = Literal["complete", "partial", "empty"]


class LaneResult(BaseModel):
    """One sub-agent/teammate lane's structured output.

    `text` has already passed `guardrails.check_output` inside the Step loop (`AgentLoop.
    _finalize_output`) before it ever lands here, so trading language cannot enter synthesis
    unscanned. `completeness`/`confidence` let synthesis tell an empty/low-confidence lane
    from a good one; `status`/`reason` carry the lane-degradation detail (provider timeout,
    quota-degraded, dropped-after-retry) surfaced as a missing dimension in the merged answer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str
    skill: str
    text: str = ""
    status: LaneStatus = "ok"
    completeness: Completeness = "complete"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str | None = None
    rich_cards: tuple[RichCard, ...] = Field(default_factory=tuple)
    web_search_sources: tuple[WebSearchSource, ...] = Field(default_factory=tuple)
    # Persisted process trace for this lane — same redacted tool-call/thinking data the live SSE
    # stream already carries, kept here so `_persist_answer` has something to write once the turn
    # ends instead of it being an ephemeral live-only concern (see `TurnResult`'s own docstring).
    tool_calls: tuple[ToolCallRecord, ...] = Field(default_factory=tuple)
    thinking_text: str = ""

    @property
    def succeeded(self) -> bool:
        """A lane counts toward synthesis only if it produced usable content — `ok`/`degraded`
        with non-empty text. `dropped`/`error`/`unavailable_tier` are missing dimensions."""
        return self.status in ("ok", "degraded") and bool(self.text.strip())


class Finding(BaseModel):
    """The structured inter-agent hand-off for `team`: a summary-quality
    decision+cause+key_figures record, never raw transcript prose — the same shape
    `conversation.models.BranchSummaryBlock` and the `finding` memory type use, so a teammate's
    hand-off bounds context growth across the sequence instead of re-inflating history.

    A lightweight sanity check runs at construction: `decision` must be non-empty, so a lane
    that produced nothing cannot silently seed a downstream teammate with an empty finding.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    skill: str
    agent_id: str
    decision: str = Field(min_length=1)
    cause: str = ""
    key_figures: tuple[str, ...] = Field(default_factory=tuple)
    source_ref: str | None = None


class SynthesisProvenance(BaseModel):
    """Which skills contributed to a synthesized answer, and which were missing — the
    provenance record journaled for the conversation-package hand-off.

    `dropped_skill_count` is how many analytical angles the classifier identified but never ran,
    combining both the `_MAX_SKILLS` routing-parse backstop and `TurnBudget.clamp_subagents`'s
    per-turn fan-out ceiling — surfaced in synthesis so a clamp is never silently absorbed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contributing_skills: tuple[str, ...] = Field(default_factory=tuple)
    missing_skills: tuple[str, ...] = Field(default_factory=tuple)
    degraded: bool = False
    dropped_skill_count: int = 0


class OrchestratorResult(BaseModel):
    """One orchestrated turn's final outcome: the caveated, guardrail-checked answer plus the
    routing decision, per-lane results, and synthesis provenance that reconstruct the fan-out."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    final_text: str
    routing: Routing
    lanes: tuple[LaneResult, ...] = Field(default_factory=tuple)
    provenance: SynthesisProvenance | None = None
    usage: TurnUsage | None = None
    rich_cards: tuple[RichCard, ...] = Field(default_factory=tuple)
    web_search_sources: tuple[WebSearchSource, ...] = Field(default_factory=tuple)


__all__ = [
    "Completeness",
    "Finding",
    "Framework",
    "LaneResult",
    "LaneStatus",
    "OrchestratorResult",
    "RichCard",
    "Routing",
    "SynthesisProvenance",
]
