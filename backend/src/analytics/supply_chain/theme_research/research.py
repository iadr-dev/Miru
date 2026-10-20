"""Step B: for each target theme, search, read, extract raw claims, append them to claims.jsonl.

Sequential and budgeted exactly like claim verification (`verification/budget.py`): a cap that is
hit ends the run gracefully and the work so far is kept. Pages come through the same
`SearchProvider` (guarded fetch: SSRF, size and timeout limits live in the gateway behind it).
A dry run searches and calls the model but writes no claim and no state.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import structlog

from analytics.supply_chain.domain.models import Trust
from analytics.supply_chain.domain.rules import host_of
from analytics.supply_chain.theme_research.extract import claims_from_reply, messages_for
from analytics.supply_chain.theme_research.files import (
    ResearchState,
    append_claims,
    claims_path,
    state_path,
    write_state,
)
from analytics.supply_chain.theme_research.models import (
    ResearchSummary,
    ThemeClaim,
    ThemeTarget,
)
from analytics.supply_chain.theme_research.targets import queries_for
from analytics.supply_chain.verification import guards
from analytics.supply_chain.verification.budget import Budget
from analytics.supply_chain.verification.directory import CompanyDirectory
from analytics.supply_chain.verification.judge import REASON_BUDGET, ask
from analytics.supply_chain.verification.search import SearchHit, SearchProvider, pick_hits
from llm.base import LLMClient

logger = structlog.get_logger(__name__)

SEARCH_COUNT = 10

DROP_FETCH_FAILED = "fetch_failed"
DROP_SOURCE_OLD = "source_too_old"
DROP_THEME_NOT_ON_PAGE = "theme_not_on_page"
DROP_LLM_ERROR = "llm_error"
DROP_BAD_JSON = "bad_json"
DROP_NO_SOURCES = "no_sources"
OUTCOME_RESEARCHED = "researched"
OUTCOME_SKIPPED = "skipped"
REASON_SEARCH_DOWN = "search_unavailable"


@dataclass(frozen=True)
class ResearchConfig:
    pages_per_target: int = 3
    max_source_age_days: int = 1095


@dataclass
class TargetResult:
    claims: list[ThemeClaim] = field(default_factory=lambda: [])
    dropped: Counter[str] = field(default_factory=Counter[str])
    skipped: str | None = None
    examined: int = 0


class ThemeResearcher:
    def __init__(  # noqa: PLR0913 - collaborators are injected, one per seam
        self,
        *,
        search: SearchProvider,
        client: LLMClient,
        directory: CompanyDirectory,
        budget: Budget,
        today: date,
        config: ResearchConfig | None = None,
        trust_table: Mapping[str, Trust] | None = None,
    ) -> None:
        self._search = search
        self._client = client
        self._directory = directory
        self.budget = budget
        self._today = today
        self._cfg = config or ResearchConfig()
        self._trust = trust_table

    async def _find_hits(self, target: ThemeTarget) -> list[SearchHit] | None:
        """The pages to read, or None when the search provider is down."""
        hits: list[SearchHit] = []
        for query in queries_for(target):
            if not self.budget.try_search():
                break
            found = await self._search.search(query, count=SEARCH_COUNT)
            if found is None:
                return None
            hits.extend(found)
        return pick_hits(hits, limit=self._cfg.pages_per_target, trust_table=self._trust)

    async def _read(  # noqa: PLR0911 - one early return per page outcome
        self, target: ThemeTarget, hit: SearchHit, result: TargetResult
    ) -> bool:
        """Read and extract one page into `result`. False = the budget stopped the target."""
        if not self.budget.try_page():
            return False
        page = await self._search.fetch(hit.url)
        if page is None:
            result.dropped[DROP_FETCH_FAILED] += 1
            return True
        published = guards.page_date(page.text, hit.published, self._today)
        if guards.source_too_old(published, self._today, self._cfg.max_source_age_days):
            result.dropped[DROP_SOURCE_OLD] += 1
            return True
        if not guards.mentions_any(page.text, target.names):
            result.dropped[DROP_THEME_NOT_ON_PAGE] += 1
            return True
        if not self.budget.try_llm("subagent"):
            return False
        result.examined += 1
        reply = await ask(self._client, messages_for(target, page), self.budget, "subagent")
        if reply is None:
            result.dropped[DROP_LLM_ERROR] += 1
            return True
        extracted = claims_from_reply(
            reply,
            target=target,
            page=page,
            directory=self._directory,
            retrieved_at=self._today.isoformat(),
            published_on=published.isoformat() if published else None,
            publisher=host_of(page.url) or page.url,
        )
        if not extracted.parsed:
            result.dropped[DROP_BAD_JSON] += 1
        result.dropped.update(extracted.dropped)
        known = {c.dedupe_key for c in result.claims}
        result.claims.extend(c for c in extracted.claims if c.dedupe_key not in known)
        return True

    async def research(self, target: ThemeTarget) -> TargetResult:
        result = TargetResult()
        chosen = await self._find_hits(target)
        if chosen is None:
            result.skipped = REASON_SEARCH_DOWN
            return result
        if not chosen:
            if self.budget.exhausted:
                result.skipped = REASON_BUDGET
            else:
                result.dropped[DROP_NO_SOURCES] += 1
            return result
        for hit in chosen:
            if not await self._read(target, hit, result):
                break
        if result.examined == 0 and not result.claims and self.budget.exhausted:
            result.skipped = REASON_BUDGET
        return result


async def run_research(  # noqa: PLR0913 - keyword-only knobs of one entry point
    targets: Sequence[ThemeTarget],
    researcher: ThemeResearcher,
    *,
    root: Path,
    state: ResearchState,
    today: date,
    dry_run: bool,
) -> ResearchSummary:
    """Research the targets in order until they or the budget run out. Claims and the state are
    written after each target, so a crash loses at most the target in flight."""
    budget = researcher.budget
    outcomes: dict[str, str] = {}
    dropped: Counter[str] = Counter()
    claims = duplicates = 0
    tried = 0
    for target in targets:
        if not budget.try_claim():
            break
        tried += 1
        result = await researcher.research(target)
        dropped.update(result.dropped)
        if result.skipped is not None:
            outcomes[target.target_id] = f"{OUTCOME_SKIPPED}:{result.skipped}"
        else:
            outcomes[target.target_id] = f"{OUTCOME_RESEARCHED}:{len(result.claims)}"
            if dry_run:
                claims += len(result.claims)
            else:
                added, dup = append_claims(claims_path(root), result.claims)
                claims += added
                duplicates += dup
                state.mark(target.kind, target.target_id, today)
                write_state(state_path(root), state)
        if budget.exhausted:
            break
    summary = ResearchSummary(
        targets=tried,
        claims=claims,
        duplicates=duplicates,
        searches=budget.searches,
        pages=budget.pages,
        llm_calls=budget.llm_calls,
        spend_usd=round(budget.spend_usd, 4),
        stopped_by=budget.stopped_by,
        dry_run=dry_run,
        dropped=dict(dropped),
        outcomes=outcomes,
    )
    logger.info(
        "sc_theme_research_summary",
        **summary.model_dump(exclude={"outcomes", "dropped"}),
        dropped=summary.dropped,
    )
    return summary


__all__ = [
    "ResearchConfig",
    "TargetResult",
    "ThemeResearcher",
    "run_research",
]
