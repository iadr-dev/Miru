"""Per-run caps. A cap that is hit ends the run gracefully: the work so far is kept and reported."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from config.settings import Settings

# Conservative per-call cost estimates in USD (a real, higher cost reported by the provider wins).
COST_SEARCH_USD: Final = 0.005
COST_PAGE_USD: Final = 0.0
COST_LLM_USD: Final[dict[str, float]] = {"subagent": 0.02, "synthesis": 0.03}

STOP_CLAIMS: Final = "claims"
STOP_SEARCHES: Final = "searches"
STOP_PAGES: Final = "pages"
STOP_LLM: Final = "llm_calls"
STOP_USD: Final = "budget_usd"


@dataclass(frozen=True)
class Limits:
    max_claims: int = 25
    max_searches: int = 60
    max_pages: int = 90
    max_llm_calls: int = 120
    budget_usd: float = 2.0

    @classmethod
    def from_settings(cls, settings: Settings) -> Limits:
        return cls(
            max_claims=settings.supply_chain_research_max_claims,
            max_searches=settings.supply_chain_research_max_searches,
            max_pages=settings.supply_chain_research_max_pages,
            max_llm_calls=settings.supply_chain_research_max_llm_calls,
            budget_usd=settings.supply_chain_research_budget_usd,
        )


@dataclass
class Budget:
    """Reserve-before-act counters. Each `try_*` charges and returns True, or refuses (and records
    which cap stopped the run) without charging."""

    limits: Limits = field(default_factory=Limits)
    claims: int = 0
    searches: int = 0
    pages: int = 0
    llm_calls: int = 0
    spend_usd: float = 0.0
    stopped_by: str | None = None

    def _refuse(self, cap: str) -> bool:
        self.stopped_by = self.stopped_by or cap
        return False

    def _afford(self, cost: float) -> bool:
        return self.spend_usd + cost <= self.limits.budget_usd + 1e-9

    def try_claim(self) -> bool:
        if self.claims >= self.limits.max_claims:
            return self._refuse(STOP_CLAIMS)
        if self.spend_usd >= self.limits.budget_usd:
            return self._refuse(STOP_USD)
        self.claims += 1
        return True

    def try_search(self) -> bool:
        if self.searches >= self.limits.max_searches:
            return self._refuse(STOP_SEARCHES)
        if not self._afford(COST_SEARCH_USD):
            return self._refuse(STOP_USD)
        self.searches += 1
        self.spend_usd += COST_SEARCH_USD
        return True

    def try_page(self) -> bool:
        if self.pages >= self.limits.max_pages:
            return self._refuse(STOP_PAGES)
        self.pages += 1
        self.spend_usd += COST_PAGE_USD
        return True

    def try_llm(self, role: str = "subagent") -> bool:
        if self.llm_calls >= self.limits.max_llm_calls:
            return self._refuse(STOP_LLM)
        if not self._afford(COST_LLM_USD.get(role, COST_LLM_USD["subagent"])):
            return self._refuse(STOP_USD)
        self.llm_calls += 1
        self.spend_usd += COST_LLM_USD.get(role, COST_LLM_USD["subagent"])
        return True

    def note_actual_llm_cost(self, role: str, cost_micros: int) -> None:
        """Top up when the provider reports more than the estimate already charged."""
        estimate = COST_LLM_USD.get(role, COST_LLM_USD["subagent"])
        actual = cost_micros / 1_000_000
        if actual > estimate:
            self.spend_usd += actual - estimate

    @property
    def exhausted(self) -> bool:
        return self.stopped_by is not None


__all__ = [
    "COST_LLM_USD",
    "COST_PAGE_USD",
    "COST_SEARCH_USD",
    "STOP_CLAIMS",
    "STOP_LLM",
    "STOP_PAGES",
    "STOP_SEARCHES",
    "STOP_USD",
    "Budget",
    "Limits",
]
