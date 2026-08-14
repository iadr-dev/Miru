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

from agent.loop import RichCard
from events.usage import TurnUsage
from llm.base import WebSearchSource

Framework = Literal["none", "single", "subagent", "team"]

_MAX_SKILLS = 4


class Routing(BaseModel):
    """The intent-classifier verdict: which framework runs the turn and which 0-4 skills feed
    it. `reason` is journaled with the `route.decided` event so a routing decision is always
    reconstructable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    framework: Framework
    skills: tuple[str, ...] = Field(default_factory=tuple)
    reason: str = Field(min_length=1)

    def bounded_skills(self) -> tuple[str, ...]:
        """The skills list clamped to the hard ceiling of 4 — a classifier that names
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
    provenance record journaled for the conversation-package hand-off."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contributing_skills: tuple[str, ...] = Field(default_factory=tuple)
    missing_skills: tuple[str, ...] = Field(default_factory=tuple)
    degraded: bool = False


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
