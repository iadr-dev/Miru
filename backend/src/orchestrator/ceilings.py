"""Per-turn failure semantics & ceilings, and the FinMind pre-flight quota-reservation seam.

Two concerns live here:

1. `TurnBudget` — the hard per-turn ceilings enforced across a fan-out: max sub-agents,
   per-agent ReAct-iteration cap, total tool-call ceiling, and per-turn cost/token ceilings.
   Exceeding a ceiling aborts to a structured degraded answer (the orchestrator surfaces it),
   never a crash. The per-agent step cap is threaded into each lane's `AgentLoop(max_steps=...)`
   (its `pre_llm`/step-boundary check already enforces it, raising `StepBudgetExceededError`);
   the sub-agent count and tool-call/cost/token ceilings are checked here before/around fan-out.

2. `QuotaReserver` — the FinMind pre-flight reservation seam. **Judgment call / dependency
   gap:** the phase brief said a quota manager already exists in `usage/` (Phase 7) to consume,
   but `src/usage/` is an empty package — no reservation function exists to call. So this module
   defines the *seam* (a `Protocol` the orchestrator depends on) plus a permissive default
   (`NoQuotaReserver`, always grants) rather than inventing a competing quota-accounting
   implementation. When the real usage-layer reserver lands it implements this Protocol and is
   injected at wiring time — no orchestrator change. `reserve(...)` returns a `QuotaReservation`
   whose `granted` lanes fan out normally and whose `degraded` lanes drop to a cached/
   `unavailable_tier` posture *before* spawning, so a fan-out never spends Sponsor budget it
   doesn't have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import structlog

from config.settings import Settings, get_settings

logger = structlog.get_logger(__name__)


class TurnCeilingExceededError(RuntimeError):
    """Raised when a per-turn ceiling is tripped. The orchestrator catches this and renders a
    structured degraded answer — it is never allowed to propagate as an unhandled crash."""

    def __init__(self, dimension: str, limit: int, attempted: int) -> None:
        self.dimension = dimension
        self.limit = limit
        self.attempted = attempted
        super().__init__(
            f"per-turn {dimension} ceiling exceeded: attempted {attempted} > limit {limit}"
        )


@dataclass
class TurnBudget:
    """Mutable per-turn budget accumulator. Not thread-safe by design — lanes run under one
    asyncio event loop, so cooperative single-threaded increments are race-free; the check
    happens at spawn/dispatch boundaries, not mid-tool."""

    max_subagents: int
    max_steps_per_agent: int
    max_tool_calls: int
    max_cost_micros: int
    max_tokens: int
    _tool_calls: int = field(default=0, init=False)
    _cost_micros: int = field(default=0, init=False)
    _tokens: int = field(default=0, init=False)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> TurnBudget:
        settings = settings or get_settings()
        return cls(
            max_subagents=settings.orch_max_subagents,
            max_steps_per_agent=settings.orch_max_steps_per_agent,
            max_tool_calls=settings.orch_max_tool_calls_per_turn,
            max_cost_micros=settings.orch_max_cost_micros_per_turn,
            max_tokens=settings.orch_max_tokens_per_turn,
        )

    def clamp_subagents(self, requested: int) -> int:
        """Clamp a requested lane count to the hard sub-agent ceiling — a fan-out never spawns
        more than `max_subagents` lanes regardless of what the classifier asked for. Bounds
        runaway fan-out cost/latency regardless of how many skills the classifier names."""
        clamped = min(requested, self.max_subagents)
        if clamped < requested:
            logger.info("orch_subagents_clamped", requested=requested, clamped=clamped)
        return clamped

    def record_tool_calls(self, count: int) -> None:
        self._tool_calls += count
        if self._tool_calls > self.max_tool_calls:
            raise TurnCeilingExceededError("tool_calls", self.max_tool_calls, self._tool_calls)

    def record_cost_micros(self, micros: int) -> None:
        self._cost_micros += micros
        if self._cost_micros > self.max_cost_micros:
            raise TurnCeilingExceededError("cost_micros", self.max_cost_micros, self._cost_micros)

    def record_tokens(self, tokens: int) -> None:
        self._tokens += tokens
        if self._tokens > self.max_tokens:
            raise TurnCeilingExceededError("tokens", self.max_tokens, self._tokens)

    @property
    def tool_calls(self) -> int:
        return self._tool_calls


@dataclass(frozen=True)
class QuotaReservation:
    """The outcome of a pre-flight FinMind reservation. `granted` skill lanes fan out with live
    Sponsor budget; `degraded` lanes spawn in a cached/`unavailable_tier` posture (a distinct
    caveated lane-degradation, not a generic error)."""

    granted: tuple[str, ...]
    degraded: tuple[str, ...] = ()

    def is_degraded(self, skill: str) -> bool:
        return skill in self.degraded


@runtime_checkable
class QuotaReserver(Protocol):
    """The pre-flight quota-reservation seam the orchestrator depends on (never a concrete
    class). A real implementation (the usage layer's FinMind pool manager, once built) reserves
    the fan-out's Sponsor budget atomically (reserve-before-act, refund on failure); this
    Protocol is the stable contract it plugs into."""

    async def reserve(self, *, skills: tuple[str, ...], tier: str) -> QuotaReservation: ...


class NoQuotaReserver:
    """The permissive default reserver: grants every lane. Used until the real usage-layer
    reserver is wired in — its presence keeps the orchestrator's fan-out path exercised and
    typed against the `QuotaReserver` Protocol, never a hardcoded 'always allow' branch."""

    async def reserve(self, *, skills: tuple[str, ...], tier: str) -> QuotaReservation:
        del tier  # accepted for the Protocol; the permissive default grants every tier alike
        return QuotaReservation(granted=skills, degraded=())


__all__ = [
    "NoQuotaReserver",
    "QuotaReservation",
    "QuotaReserver",
    "TurnBudget",
    "TurnCeilingExceededError",
]
