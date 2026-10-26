"""Wiring for Steps B and C: gather inputs, run, write proposal files, journal. Shared by the
`research-themes` / `verify-themes` operator commands and the monthly `sc_theme_research` job.

Nothing here edits a live concept-theme file and nothing calls `promote`: the outputs are claims
and proposal files for a person to review. Output and journal lines carry counts and ids only.

Job gates (each journals SKIPPED, never an error): SUPPLY_CHAIN_RESEARCH_ENABLED is off, this
process is not the ingest leader, no search key, or no LLM role.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import anyio.to_thread
import structlog

from analytics.supply_chain.domain.concepts import EntityType
from analytics.supply_chain.refdata_io.loader import TRUST_FILE, load_trust_table
from analytics.supply_chain.theme_discovery.io import ThemeMiningError
from analytics.supply_chain.theme_research.files import (
    CoveredRecord,
    append_covered,
    append_rejections,
    claims_path,
    covered_path,
    read_claims,
    read_covered,
    read_rejected_keys,
    read_state,
    rejected_path,
    state_path,
)
from analytics.supply_chain.theme_research.models import (
    ResearchSummary,
    ThemeClaim,
    VerifySummary,
)
from analytics.supply_chain.theme_research.proposals import (
    MIN_NEW_THEME_MEMBERS,
    Proposed,
    ProposedMember,
    ProposedTheme,
    WriteResult,
    proposal_paths,
    scan_proposed,
    theme_key_for,
    write_proposals,
)
from analytics.supply_chain.theme_research.research import ResearchConfig, ThemeResearcher
from analytics.supply_chain.theme_research.research import run_research as _run_research
from analytics.supply_chain.theme_research.targets import read_catalog, select_targets
from analytics.supply_chain.theme_research.verify import (
    COVERED_THRESHOLD,
    REASON_ALREADY_COVERED,
    REASON_ALREADY_PROPOSED,
    REASON_ALREADY_REJECTED,
    RELATED_FLOOR,
    GroupResult,
    ThemeInfo,
    VerifiedMember,
    VerifyContext,
    best_cover,
    evaluate_group,
    group_claims,
)
from analytics.supply_chain.themes_io.loader import (
    CONCEPTS_SUBDIR,
    ConceptFileError,
    parse_concept_files,
)
from analytics.supply_chain.themes_io.promote import ProposalFileError
from analytics.supply_chain.verification.budget import Budget, Limits
from analytics.supply_chain.verification.directory import CompanyDirectory, load_directory
from analytics.supply_chain.verification.job import missing_prerequisite, research_model
from analytics.supply_chain.verification.search import BraveSearchProvider, SearchProvider
from config.private_data import reference_dir
from config.settings import Settings, get_settings
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run
from ingest.store import ParquetStore
from llm.base import LLMClient
from llm.registry import ModelRole, assert_no_byok_scope, resolve_role_with_override

logger = structlog.get_logger(__name__)

SC_THEME_RESEARCH_DATASET = "sc_theme_research"
FILE_ERRORS = (ConceptFileError, ProposalFileError, ThemeMiningError, OSError)


def _judge_client(settings: Settings) -> LLMClient:
    return resolve_role_with_override(ModelRole.SUBAGENT, research_model(settings), settings)


def _second_client(settings: Settings) -> LLMClient | None:
    if not settings.supply_chain_second_opinion:
        return None
    spec = settings.supply_chain_research_second_model.strip() or None
    return resolve_role_with_override(ModelRole.SYNTHESIS, spec, settings)


async def execute_research(  # noqa: PLR0913 - collaborators are injected; one per seam
    settings: Settings,
    store: ParquetStore,
    *,
    today: date,
    source: str,
    limit: int | None,
    dry_run: bool,
    search: SearchProvider | None = None,
    client: LLMClient | None = None,
    directory: CompanyDirectory | None = None,
    root: Path | None = None,
) -> ResearchSummary:
    """Step B. Raises `ConceptFileError` (and OSError) on an unusable file."""
    assert_no_byok_scope()
    base = root or reference_dir()
    state = read_state(state_path(base))
    wanted = limit if limit is not None else settings.supply_chain_research_max_claims
    targets = await anyio.to_thread.run_sync(
        lambda: select_targets(base, state, source=source, limit=wanted)
    )
    known = (
        directory
        if directory is not None
        else await anyio.to_thread.run_sync(lambda: load_directory(store, base))
    )
    trust = load_trust_table(base.joinpath(*TRUST_FILE))
    researcher = ThemeResearcher(
        search=search or BraveSearchProvider(tls_policy=settings.tls_policy),
        client=client or _judge_client(settings),
        directory=known,
        budget=Budget(Limits.from_settings(settings)),
        today=today,
        config=ResearchConfig(
            pages_per_target=settings.supply_chain_pages_per_claim,
            max_source_age_days=settings.supply_chain_max_source_age_days,
        ),
        trust_table=trust,
    )
    return await _run_research(
        targets, researcher, root=base, state=state, today=today, dry_run=dry_run
    )


def _build_context(
    settings: Settings, base: Path, directory: CompanyDirectory, today: date
) -> VerifyContext:
    concepts = base.joinpath(*CONCEPTS_SUBDIR)
    themes = parse_concept_files(concepts)[0] if concepts.is_dir() else []
    catalog = read_catalog(base)
    aliases = {e.id: e.aliases for e in catalog}
    return VerifyContext(
        today=today,
        max_age_days=settings.supply_chain_max_source_age_days,
        directory_lookup=lambda symbol: directory.get("TW", symbol),
        universe=frozenset(
            e.symbol
            for e in catalog
            if e.type is EntityType.TW_COMPANY and e.symbol and e.market == "TW"
        ),
        themes={
            t.key: ThemeInfo(
                names=tuple(n for n in (t.name_zh, t.name_en) if n),
                members=frozenset(m.symbol for m in t.members),
            )
            for t in themes
        },
        aliases_of=lambda label: aliases.get(label, ()),
        trust_table=load_trust_table(base.joinpath(*TRUST_FILE)),
        second_opinion=settings.supply_chain_second_opinion,
    )


def _proposed_member(member: VerifiedMember) -> ProposedMember:
    ev = member.evidence
    return ProposedMember(
        symbol=member.company.symbol,
        name=member.company.name,
        position=member.position,
        publisher=ev.publisher,
        url=ev.url,
        excerpt=ev.excerpt,
        confidence=member.confidence,
    )


def _candidate_key(member: VerifiedMember, ctx: VerifyContext) -> tuple[str, str]:
    return theme_key_for(member.theme, [member.label, *ctx.aliases_of(member.label)])


@dataclass
class Plan:
    themes: list[ProposedTheme] = field(default_factory=lambda: [])
    additions: list[tuple[str, ProposedMember]] = field(default_factory=lambda: [])
    pending: int = 0
    covered: list[CoveredRecord] = field(default_factory=lambda: [])
    notes: list[str] = field(default_factory=lambda: [])


def _plan_candidate(  # noqa: PLR0913, PLR0917 - one candidate and its decision inputs
    plan: Plan,
    candidate: str,
    members: list[VerifiedMember],
    ctx: VerifyContext,
    prior: CoveredRecord | None,
    threshold: float,
) -> None:
    """Coverage check, then the usual new-theme rule. The verified set includes the symbols an
    earlier run already found covered, so a re-run judges the whole candidate."""
    key, name_en = _candidate_key(members[0], ctx)
    symbols = {m.company.symbol for m in members} | set(prior.symbols if prior else ())
    cover = best_cover(symbols, ctx.themes)
    related: list[str] = []
    if cover is not None:
        covering, overlap = cover
        share = overlap / len(symbols)
        tally = f"{overlap}/{len(symbols)}"
        if share >= threshold:
            held = ctx.themes[covering].members
            fresh = [m for m in members if m.company.symbol not in held]
            plan.additions.extend((covering, _proposed_member(m)) for m in fresh)
            plan.covered.append(
                CoveredRecord(candidate, covering, share, len(symbols), len(fresh), tuple(symbols))
            )
            plan.notes.append(f"candidate {candidate}: covered by {covering} ({tally})")
            return
        if share >= RELATED_FLOOR:
            related = [covering]
            plan.notes.append(f"candidate {candidate}: partly covered by {covering} ({tally})")
    if len(members) >= MIN_NEW_THEME_MEMBERS:
        plan.themes.append(
            ProposedTheme(
                key=key,
                name_zh=members[0].label,
                name_en=name_en,
                members=[_proposed_member(m) for m in members],
                related=related,
            )
        )
    else:
        plan.pending += 1


def _plan(
    verified: list[VerifiedMember],
    ctx: VerifyContext,
    proposed: Proposed,
    covered: dict[str, CoveredRecord],
    threshold: float,
) -> Plan:
    """New theme rows, member rows, candidates below the member threshold, covered decisions."""
    plan = Plan()
    by_candidate: dict[str, list[VerifiedMember]] = {}
    for m in verified:
        if m.kind == "existing":
            plan.additions.append((m.theme, _proposed_member(m)))
        else:
            by_candidate.setdefault(m.theme, []).append(m)
    for candidate, members in by_candidate.items():
        key, _ = _candidate_key(members[0], ctx)
        if key in proposed.theme_keys or key in ctx.themes:
            plan.additions.extend((key, _proposed_member(m)) for m in members)
        else:
            _plan_candidate(plan, candidate, members, ctx, covered.get(candidate), threshold)
    return plan


def _already_proposed(group: list[ThemeClaim], ctx: VerifyContext, proposed: Proposed) -> bool:
    first = group[0]
    key = first.theme
    if first.kind == "candidate":
        key = theme_key_for(first.theme, [first.label, *ctx.aliases_of(first.label)])[0]
    return (key, first.symbol) in proposed.pairs


def _already_covered(group: list[ThemeClaim], covered: dict[str, CoveredRecord]) -> bool:
    first = group[0]
    record = covered.get(first.theme) if first.kind == "candidate" else None
    return record is not None and first.symbol in record.symbols


@dataclass
class _Judged:
    """What the judged groups of one verify pass produced."""

    outcomes: dict[str, str] = field(default_factory=lambda: {})
    counts: Counter[str] = field(default_factory=Counter[str])
    verified: list[VerifiedMember] = field(default_factory=lambda: [])
    rejections: list[tuple[ThemeClaim, str]] = field(default_factory=lambda: [])

    def add(self, result: GroupResult, reasons: Counter[str]) -> None:
        self.outcomes[result.group_id] = result.status
        self.counts[result.status] += 1
        if result.reason:
            reasons[result.reason] += 1
        self.rejections.extend(result.rejections)
        if result.member is not None:
            self.verified.append(result.member)


@dataclass
class _GroupLoop:
    """Judges claim groups in order until the group cap or the claim budget runs out."""

    ctx: VerifyContext
    proposed: Proposed
    covered: dict[str, CoveredRecord]
    budget: Budget
    judge: LLMClient | None
    wanted: int
    reasons: Counter[str]

    def skip_reason(self, group: list[ThemeClaim]) -> str | None:
        if _already_proposed(group, self.ctx, self.proposed):
            return REASON_ALREADY_PROPOSED
        if _already_covered(group, self.covered):
            return REASON_ALREADY_COVERED
        return None

    async def run(self, groups: list[list[ThemeClaim]]) -> _Judged:
        judged = _Judged()
        for group in groups:
            if len(judged.outcomes) >= self.wanted:
                break
            skip = self.skip_reason(group)
            if skip is not None:
                self.reasons[skip] += 1
                continue
            if not self.budget.try_claim():
                break
            judged.add(await evaluate_group(group, self.ctx, self.budget, self.judge), self.reasons)
        return judged


def _fresh_claims(
    base: Path, rejected: set[tuple[str, str, str]], reasons: Counter[str]
) -> list[ThemeClaim]:
    """The stored claims minus those a person or an earlier pass already rejected (counted)."""
    fresh: list[ThemeClaim] = []
    for claim in read_claims(claims_path(base)):
        if claim.dedupe_key in rejected:
            reasons[REASON_ALREADY_REJECTED] += 1
        else:
            fresh.append(claim)
    return fresh


def _verify_summary(  # noqa: PLR0913 - one value per summary input
    judged: _Judged,
    plan: Plan,
    written: WriteResult,
    budget: Budget,
    reasons: Counter[str],
    *,
    dry_run: bool,
) -> VerifySummary:
    return VerifySummary(
        groups=len(judged.outcomes),
        verified=judged.counts["verified"],
        candidates=judged.counts["candidate"],
        rejected=judged.counts["rejected"],
        skipped=judged.counts["skipped"],
        member_rows=written.member_rows,
        new_themes=written.new_themes,
        pending_themes=plan.pending,
        covered=len(plan.covered),
        llm_calls=budget.llm_calls,
        spend_usd=round(budget.spend_usd, 4),
        stopped_by=budget.stopped_by,
        dry_run=dry_run,
        notes=plan.notes,
        reasons=dict(reasons),
        outcomes=judged.outcomes,
    )


async def execute_verify(  # noqa: PLR0913 - collaborators are injected; one per seam
    settings: Settings,
    store: ParquetStore,
    *,
    today: date,
    limit: int | None,
    dry_run: bool,
    second_client: LLMClient | None = None,
    directory: CompanyDirectory | None = None,
    root: Path | None = None,
    covered_threshold: float = COVERED_THRESHOLD,
) -> VerifySummary:
    """Step C. Raises `ConceptFileError` / `ProposalFileError` (and OSError) on an unusable file."""
    assert_no_byok_scope()
    base = root or reference_dir()
    known = (
        directory
        if directory is not None
        else await anyio.to_thread.run_sync(lambda: load_directory(store, base))
    )
    ctx = _build_context(settings, base, known, today)
    rejected = read_rejected_keys(rejected_path(base))
    proposed = scan_proposed(base)
    covered = read_covered(covered_path(base))
    reasons: Counter[str] = Counter()
    fresh = _fresh_claims(base, rejected, reasons)
    groups = list(group_claims(fresh).values())
    judge = second_client if second_client is not None else _second_client(settings)
    budget = Budget(Limits.from_settings(settings))
    wanted = limit if limit is not None else settings.supply_chain_research_max_claims
    loop = _GroupLoop(ctx, proposed, covered, budget, judge, wanted, reasons)
    judged = await loop.run(groups)
    plan = _plan(judged.verified, ctx, proposed, covered, covered_threshold)
    written = write_proposals(
        proposal_paths(base, today), themes=plan.themes, additions=plan.additions, dry_run=dry_run
    )
    if not dry_run:
        append_covered(covered_path(base), plan.covered, on=today)
        append_rejections(rejected_path(base), judged.rejections, on=today)
    summary = _verify_summary(judged, plan, written, budget, reasons, dry_run=dry_run)
    logger.info(
        "sc_theme_verify_summary",
        **summary.model_dump(exclude={"outcomes", "reasons", "notes"}),
        reasons=summary.reasons,
    )
    return summary


def format_research(summary: ResearchSummary) -> str:
    lines = [summary.line()]
    lines.extend(f"{target_id} {outcome}" for target_id, outcome in summary.outcomes.items())
    return "\n".join(lines) + "\n"


def format_verify(summary: VerifySummary) -> str:
    lines = [summary.line(), *summary.notes]
    lines.extend(f"{group_id} {status}" for group_id, status in summary.outcomes.items())
    return "\n".join(lines) + "\n"


def command_blocker(settings: Settings, command: str) -> str | None:
    need_second = command == "verify-themes" and settings.supply_chain_second_opinion
    return missing_prerequisite(settings, need_second=need_second)


async def run_theme_command(
    command: str,
    *,
    limit: int | None,
    dry_run: bool,
    source: str,
    covered_threshold: float = COVERED_THRESHOLD,
) -> int:
    """`research-themes` / `verify-themes`. A real pass refuses without a search key and a model;
    a dry run needs them too, because it searches and judges."""
    settings = get_settings()
    blocker = command_blocker(settings, command)
    if blocker is not None:
        sys.stderr.write(f"cannot run {command}: {blocker}\n")
        return 1
    today = datetime.now(UTC).date()
    store = ParquetStore(settings.parquet_root)
    try:
        if command == "research-themes":
            research = await execute_research(
                settings, store, today=today, source=source, limit=limit, dry_run=dry_run
            )
            sys.stdout.write(format_research(research))
        else:
            verify = await execute_verify(
                settings,
                store,
                today=today,
                limit=limit,
                dry_run=dry_run,
                covered_threshold=covered_threshold,
            )
            sys.stdout.write(format_verify(verify))
    except FILE_ERRORS as exc:
        sys.stderr.write(f"invalid file: {exc}\n")
        return 1
    return 0


def _journal(run_date: date, status: RunStatus, detail: str, rows: int = 0) -> JobRun:
    return journal_run(
        SC_THEME_RESEARCH_DATASET, run_date, rows_written=rows, status=status, detail=detail
    )


async def run_theme_research_job(  # noqa: PLR0913 - collaborators are injected; one per seam
    run_date: date,
    ctx: IngestContext,
    *,
    settings: Settings,
    search: SearchProvider | None = None,
    client: LLMClient | None = None,
    second_client: LLMClient | None = None,
    directory: CompanyDirectory | None = None,
    root: Path | None = None,
    dry_run: bool = False,
) -> JobRun:
    """B then C with the run caps. Writes claims and proposal files only; never promotes."""
    assert_no_byok_scope()
    if not settings.supply_chain_research_enabled:
        return _journal(run_date, RunStatus.SKIPPED, "SUPPLY_CHAIN_RESEARCH_ENABLED is off")
    if not ctx.leader.is_leader():
        return _journal(run_date, RunStatus.SKIPPED, "not the leader - write skipped")
    if search is None or client is None:
        blocker = command_blocker(settings, "verify-themes")
        if blocker is not None:
            return _journal(run_date, RunStatus.SKIPPED, blocker)
    try:
        research = await execute_research(
            settings,
            ctx.store,
            today=run_date,
            source="all",
            limit=None,
            dry_run=dry_run,
            search=search,
            client=client,
            directory=directory,
            root=root,
        )
        verify = await execute_verify(
            settings,
            ctx.store,
            today=run_date,
            limit=None,
            dry_run=dry_run,
            second_client=second_client,
            directory=directory,
            root=root,
        )
    except FILE_ERRORS as exc:
        logger.error("sc_theme_research_invalid", error=str(exc))
        return _journal(run_date, RunStatus.FAILED, f"invalid file or write failure: {exc}")
    detail = f"research: {research.line()} | verify: {verify.line()}"
    rows = research.claims + verify.member_rows + verify.new_themes
    return _journal(run_date, RunStatus.SUCCESS, detail, rows)


async def job_sc_theme_research(run_date: date, ctx: IngestContext) -> JobRun:
    """Weekly, leader-only, off unless SUPPLY_CHAIN_RESEARCH_ENABLED; capped by the run budget.
    Writes claims and proposal files only: a person reviews them and runs `promote`."""
    return await run_theme_research_job(run_date, ctx, settings=get_settings())


__all__ = [
    "SC_THEME_RESEARCH_DATASET",
    "execute_research",
    "execute_verify",
    "format_research",
    "format_verify",
    "job_sc_theme_research",
    "run_theme_command",
    "run_theme_research_job",
]
