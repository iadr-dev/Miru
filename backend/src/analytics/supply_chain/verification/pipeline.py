"""search -> read -> judge -> guard -> grade -> write, for a list of targets, inside a budget.

Sequential on purpose: one claim at a time keeps the search provider's rate and the spend easy to
reason about. Every claim ends as verified, candidate, rejected (examined, not supported: logged
for the cooldown) or skipped (not examined: budget, provider down).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import anyio.to_thread
import structlog

from analytics.supply_chain.domain.models import Evidence, Grade, Status, Trust
from analytics.supply_chain.domain.rules import grade_from_evidence, independence_key
from analytics.supply_chain.verification.budget import Budget
from analytics.supply_chain.verification.directory import CompanyDirectory
from analytics.supply_chain.verification.judge import (
    REASON_BUDGET,
    Assessment,
    JudgeContext,
    assess_gap,
    assess_pair,
)
from analytics.supply_chain.verification.models import (
    ClaimOutcome,
    ClaimResult,
    Company,
    OutcomeKind,
    Page,
    RunSummary,
    Target,
)
from analytics.supply_chain.verification.rejected_log import append_rejection
from analytics.supply_chain.verification.search import SearchHit, SearchProvider, find_hits
from analytics.supply_chain.verification.writer import (
    NotLeaderError,
    RelationWriter,
    WriterError,
)
from llm.base import LLMClient

logger = structlog.get_logger(__name__)

_MIN_TERTIARY_PUBLISHERS = 2

REASON_NO_SOURCES = "no_sources"
REASON_SEARCH_DOWN = "search_unavailable"
REASON_FETCH_FAILED = "fetch_failed"
REASON_WRITE_FAILED = "write_failed"
REASON_SECOND_OPINION = "second_opinion_disagreed"
STOP_NOT_LEADER = "not_leader"


@dataclass(frozen=True)
class PipelineConfig:
    pages_per_claim: int = 3
    max_source_age_days: int = 1095
    second_opinion: bool = False


@dataclass
class _Collected:
    supplier: Company
    customer: Company
    target: Target
    evidence: list[Evidence] = field(default_factory=lambda: [])
    pages: list[Page] = field(default_factory=lambda: [])
    years: list[int] = field(default_factory=lambda: [])


def _as_of(evidence: Sequence[Evidence], years: Sequence[int]) -> date | None:
    dated = [e.published_on for e in evidence if e.published_on is not None]
    if dated:
        return max(dated)
    return date(max(years), 1, 1) if years else None


class Pipeline:
    def __init__(  # noqa: PLR0913 - collaborators are injected, one per seam
        self,
        *,
        search: SearchProvider,
        judge_client: LLMClient,
        directory: CompanyDirectory,
        budget: Budget,
        today: date,
        config: PipelineConfig | None = None,
        second_client: LLMClient | None = None,
        trust_table: Mapping[str, Trust] | None = None,
    ) -> None:
        self._search = search
        self._client = judge_client
        self._second = second_client
        self._directory = directory
        self.budget = budget
        self._today = today
        self._cfg = config or PipelineConfig()
        self._trust = trust_table
        self.reasons: Counter[str] = Counter()
        self._claim_reasons: Counter[str] = Counter()

    def _ctx(self) -> JudgeContext:
        return JudgeContext(self._today, self._cfg.max_source_age_days, self._trust)

    async def _fetch(self, hit: SearchHit) -> Page | None:
        if not self.budget.try_page():
            return None
        page = await self._search.fetch(hit.url)
        if page is None:
            self._bump(REASON_FETCH_FAILED)
            return None
        return page.model_copy(update={"hit_published": hit.published})

    def _note(self, assessment: Assessment) -> None:
        if not assessment.accepted:
            self._bump(assessment.reason)

    def _bump(self, reason: str) -> None:
        self.reasons[reason] += 1
        self._claim_reasons[reason] += 1

    async def verify_target(self, target: Target) -> ClaimOutcome:
        self._claim_reasons = Counter()
        hits = await find_hits(
            self._search,
            target,
            self.budget,
            pages_per_claim=self._cfg.pages_per_claim,
            trust_table=self._trust,
        )
        if hits is None:
            self._bump(REASON_SEARCH_DOWN)
            return ClaimOutcome(
                claim_id=target.claim_id, kind=OutcomeKind.SKIPPED, reason=REASON_SEARCH_DOWN
            )
        if not hits:
            if self.budget.exhausted:
                return ClaimOutcome(
                    claim_id=target.claim_id, kind=OutcomeKind.SKIPPED, reason=REASON_BUDGET
                )
            self._bump(REASON_NO_SOURCES)
            return ClaimOutcome(
                claim_id=target.claim_id, kind=OutcomeKind.REJECTED, reason=REASON_NO_SOURCES
            )
        if target.customer is None:
            return await self._verify_gap(target, hits)
        return await self._verify_pair(target, target.customer, hits)

    async def _verify_pair(
        self, target: Target, customer: Company, hits: list[SearchHit]
    ) -> ClaimOutcome:
        collected = _Collected(target.supplier, customer, target)
        examined = 0
        for hit in hits:
            page = await self._fetch(hit)
            if page is None:
                if self.budget.exhausted:
                    break
                continue
            assessment = await assess_pair(
                self._client, target, customer, page, ctx=self._ctx(), budget=self.budget
            )
            self._note(assessment)
            examined += int(assessment.reason != REASON_BUDGET)
            if assessment.accepted and assessment.evidence is not None:
                collected.evidence.append(assessment.evidence)
                collected.pages.append(page)
                if assessment.as_of_year is not None:
                    collected.years.append(assessment.as_of_year)
                if grade_from_evidence(collected.evidence) is Grade.HIGH:
                    break
            elif assessment.reason == REASON_BUDGET:
                break
        if not collected.evidence:
            return self._unsupported(target, examined)
        return ClaimOutcome(
            claim_id=target.claim_id,
            kind=OutcomeKind.VERIFIED,
            results=(await self._finish(collected),),
        )

    def _unsupported(self, target: Target, examined: int) -> ClaimOutcome:
        if examined == 0 and self.budget.exhausted:
            return ClaimOutcome(
                claim_id=target.claim_id, kind=OutcomeKind.SKIPPED, reason=REASON_BUDGET
            )
        top = self._claim_reasons.most_common(1)
        reason = top[0][0] if top else REASON_NO_SOURCES
        return ClaimOutcome(claim_id=target.claim_id, kind=OutcomeKind.REJECTED, reason=reason)

    def note_write_failure(self) -> None:
        self._bump(REASON_WRITE_FAILED)

    async def _finish(self, collected: _Collected) -> ClaimResult:
        """Grade the evidence; a weak single-source claim may get a second opinion."""
        grade = grade_from_evidence(collected.evidence) or Grade.LOW
        status = status_for(collected.evidence)
        if (
            status is Status.VERIFIED
            and self._cfg.second_opinion
            and self._second is not None
            and grade is Grade.LOW
            and len(collected.evidence) == 1
            and self.budget.try_llm("synthesis")
        ):
            second = await assess_pair(
                self._second,
                collected.target,
                collected.customer,
                collected.pages[0],
                ctx=self._ctx(),
                budget=self.budget,
                role="synthesis",
                charge=False,
            )
            if not second.accepted:
                status = Status.CANDIDATE
                self._bump(REASON_SECOND_OPINION)
        return ClaimResult(
            supplier=collected.supplier,
            customer=collected.customer,
            kind=collected.target.kind,
            status=status,
            grade=grade,
            product=collected.target.product,
            as_of=_as_of(collected.evidence, collected.years),
            evidence=tuple(collected.evidence),
        )

    async def _verify_gap(self, target: Target, hits: list[SearchHit]) -> ClaimOutcome:
        found: dict[str, _Collected] = {}
        examined = 0
        for hit in hits:
            page = await self._fetch(hit)
            if page is None:
                if self.budget.exhausted:
                    break
                continue
            findings, page_reason = await assess_gap(
                self._client, target, page, self._directory, ctx=self._ctx(), budget=self.budget
            )
            examined += int(page_reason != REASON_BUDGET)
            if page_reason:
                self._bump(page_reason)
                if page_reason == REASON_BUDGET:
                    break
            for finding in findings:
                self._note(finding.assessment)
                if not (finding.assessment.accepted and finding.assessment.evidence):
                    continue
                entry = found.setdefault(
                    finding.target.claim_id,
                    _Collected(finding.target.supplier, finding.customer, finding.target),
                )
                entry.evidence.append(finding.assessment.evidence)
                if finding.assessment.as_of_year is not None:
                    entry.years.append(finding.assessment.as_of_year)
        results = tuple([await self._finish_plain(c) for c in found.values()])
        if not results:
            return self._unsupported(target, examined)
        return ClaimOutcome(claim_id=target.claim_id, kind=OutcomeKind.VERIFIED, results=results)

    async def _finish_plain(self, collected: _Collected) -> ClaimResult:
        grade = grade_from_evidence(collected.evidence) or Grade.LOW
        return ClaimResult(
            supplier=collected.supplier,
            customer=collected.customer,
            kind=collected.target.kind,
            status=status_for(collected.evidence),
            grade=grade,
            as_of=_as_of(collected.evidence, collected.years),
            evidence=tuple(collected.evidence),
        )


def status_for(evidence: Sequence[Evidence]) -> Status:
    """Unattended research publishes only what a reliable source backs: a primary or secondary
    publisher, or two independent tertiary ones. Anything resting on a single tertiary source
    (including any unlisted host) is written as a candidate for a person or a second source."""
    reliable = any(e.trust in (Trust.PRIMARY, Trust.SECONDARY) for e in evidence)
    tertiary = {independence_key(e) for e in evidence if e.trust is Trust.TERTIARY}
    return (
        Status.VERIFIED
        if reliable or len(tertiary) >= _MIN_TERTIARY_PUBLISHERS
        else Status.CANDIDATE
    )


async def run_pipeline(  # noqa: PLR0913 - collaborators are injected, one per seam
    targets: Sequence[Target],
    pipeline: Pipeline,
    *,
    writer: RelationWriter | None,
    rejected_file: Path | None,
    today: date,
    dry_run: bool,
) -> RunSummary:
    """Run the targets in order until they or the budget run out. Writes after each claim so a
    crash loses at most the claim in flight."""
    budget = pipeline.budget
    verified = candidates = rejected = skipped = written = 0
    outcomes: dict[str, str] = {}
    stopped: str | None = None
    for target in targets:
        if not budget.try_claim():
            break
        outcome = await pipeline.verify_target(target)
        outcomes[target.claim_id] = outcome.kind.value
        if outcome.kind is OutcomeKind.VERIFIED:
            has_candidate = any(r.status is Status.CANDIDATE for r in outcome.results)
            verified += int(not has_candidate)
            candidates += int(has_candidate)
            if writer is not None:
                try:
                    report = await anyio.to_thread.run_sync(writer.apply, outcome.results)
                    written += report.rows_written
                except NotLeaderError:
                    stopped = STOP_NOT_LEADER
                    break
                except WriterError as exc:
                    pipeline.note_write_failure()
                    logger.warning(
                        "sc_research_write_failed", claim_id=target.claim_id, error=str(exc)
                    )
        elif outcome.kind is OutcomeKind.REJECTED:
            rejected += 1
            if rejected_file is not None and not dry_run:
                append_rejection(
                    rejected_file, claim_id=target.claim_id, on=today, reason=outcome.reason
                )
        else:
            skipped += 1
        if budget.exhausted:
            break
    return RunSummary(
        claims_tried=len(outcomes),
        verified=verified,
        candidates=candidates,
        rejected=rejected,
        skipped=skipped,
        searches=budget.searches,
        pages=budget.pages,
        llm_calls=budget.llm_calls,
        spend_usd=round(budget.spend_usd, 4),
        stopped_by=stopped or budget.stopped_by,
        dry_run=dry_run,
        rows_written=written,
        reasons=dict(pipeline.reasons),
        outcomes=outcomes,
    )


__all__ = [
    "Pipeline",
    "PipelineConfig",
    "run_pipeline",
    "status_for",
]
