"""Wiring: gather the inputs, run the pipeline, reindex, journal. Shared by the weekly job, the dev
backfill step and the operator CLI.

Gates for the job (each journals SKIPPED, never an error): the setting is off, this process is not
the ingest leader, no search key, or no LLM role configured. A run never journals page text.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import anyio.to_thread
import structlog

from analytics.supply_chain.domain.models import Relation
from analytics.supply_chain.jobs import job_sc_refdata_load
from analytics.supply_chain.refdata_io.loader import (
    RELATIONS_SUBDIR,
    TRUST_FILE,
    RelationFileError,
    load_trust_table,
    parse_relation_files,
)
from analytics.supply_chain.verification.budget import Budget, Limits
from analytics.supply_chain.verification.directory import CompanyDirectory, load_directory
from analytics.supply_chain.verification.discovery import (
    CANDIDATES_FILE,
    DiscoveryConfig,
    configured_sources,
    discover_targets,
    read_candidates,
)
from analytics.supply_chain.verification.models import RunSummary, Target
from analytics.supply_chain.verification.pipeline import Pipeline, PipelineConfig, run_pipeline
from analytics.supply_chain.verification.rejected_log import (
    load_recent_rejections,
    rejected_path,
)
from analytics.supply_chain.verification.search import BraveSearchProvider, SearchProvider
from analytics.supply_chain.verification.writer import RelationWriter, WriterError
from config.private_data import reference_dir
from config.settings import Settings, get_settings, reveal
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run
from ingest.store import ParquetStore
from llm.base import LLMClient
from llm.registry import (
    ModelRole,
    ProviderNotConfiguredError,
    assert_no_byok_scope,
    resolve_role_with_override,
)

logger = structlog.get_logger(__name__)

SC_RESEARCH_DATASET = "sc_research"


def _read_published(root: Path, today: date) -> list[Relation]:
    directory = root.joinpath(*RELATIONS_SUBDIR)
    if not directory.is_dir() or not any(directory.glob("*.yaml")):
        return []
    trust = load_trust_table(root.joinpath(*TRUST_FILE))
    return parse_relation_files(directory, trust, today).relations


def discovery_config(settings: Settings, *, source: str, limit: int | None) -> DiscoveryConfig:
    return DiscoveryConfig(
        limit=limit if limit is not None else settings.supply_chain_research_max_claims,
        sources=configured_sources(source),
        reverify_days=settings.supply_chain_reverify_days,
        cooldown_days=settings.supply_chain_reject_cooldown_days,
        gap_top_n=settings.supply_chain_gap_top_n,
    )


def prepare(  # noqa: PLR0913 - keyword-only inputs
    settings: Settings,
    store: ParquetStore,
    root: Path,
    today: date,
    *,
    source: str,
    limit: int | None,
) -> tuple[list[Target], CompanyDirectory, list[Relation]]:
    """Read-only: the files and stored datasets in, the ordered targets out. Blocking disk work."""
    published = _read_published(root, today)
    directory = load_directory(store, root, published)
    config = discovery_config(settings, source=source, limit=limit)
    rejected = load_recent_rejections(
        rejected_path(root), today=today, cooldown_days=config.cooldown_days
    )
    targets = discover_targets(
        directory=directory,
        candidates=read_candidates(root.joinpath(*CANDIDATES_FILE)),
        published=published,
        rejected=rejected,
        today=today,
        config=config,
    )
    return targets, directory, published


def research_model(settings: Settings) -> str | None:
    """The research judge model spec; a blank value (compose passes one) means the subagent role."""
    return (settings.supply_chain_research_model or "").strip() or None


def _second_model(settings: Settings) -> str | None:
    """The second-opinion model spec; a blank value means the SYNTHESIS role chain."""
    return settings.supply_chain_research_second_model.strip() or None


def missing_prerequisite(settings: Settings, *, need_second: bool = False) -> str | None:
    """Why a real run cannot start (no search key, no LLM role), or None."""
    if not reveal(settings.brave_search_api_key):
        return "no search key configured"
    try:
        resolve_role_with_override(ModelRole.SUBAGENT, research_model(settings), settings)
        if need_second:
            resolve_role_with_override(ModelRole.SYNTHESIS, _second_model(settings), settings)
    except ProviderNotConfiguredError:
        return "no LLM role configured for the verification model"
    return None


async def execute_research(  # noqa: PLR0913 - collaborators are injected, one per seam
    settings: Settings,
    store: ParquetStore,
    *,
    today: date,
    source: str,
    limit: int | None,
    dry_run: bool,
    is_leader: Callable[[], bool],
    search: SearchProvider | None = None,
    judge_client: LLMClient | None = None,
    second_client: LLMClient | None = None,
    root: Path | None = None,
) -> RunSummary:
    """Discover, verify and (unless dry) write. Raises RelationFileError on a bad relation file."""
    assert_no_byok_scope()
    base = root or reference_dir()
    targets, directory, _ = await anyio.to_thread.run_sync(
        lambda: prepare(settings, store, base, today, source=source, limit=limit)
    )
    trust = load_trust_table(base.joinpath(*TRUST_FILE))
    pipeline = Pipeline(
        search=search or BraveSearchProvider(tls_policy=settings.tls_policy),
        judge_client=judge_client
        or resolve_role_with_override(ModelRole.SUBAGENT, research_model(settings), settings),
        second_client=second_client
        or (
            resolve_role_with_override(ModelRole.SYNTHESIS, _second_model(settings), settings)
            if settings.supply_chain_second_opinion
            else None
        ),
        directory=directory,
        budget=Budget(Limits.from_settings(settings)),
        today=today,
        config=PipelineConfig(
            pages_per_claim=settings.supply_chain_pages_per_claim,
            max_source_age_days=settings.supply_chain_max_source_age_days,
            second_opinion=settings.supply_chain_second_opinion,
        ),
        trust_table=trust,
    )
    writer = RelationWriter(
        base.joinpath(*RELATIONS_SUBDIR),
        is_leader=is_leader,
        today=today,
        trust_table=trust,
        dry_run=dry_run,
    )
    summary = await run_pipeline(
        targets,
        pipeline,
        writer=writer,
        rejected_file=rejected_path(base),
        today=today,
        dry_run=dry_run,
    )
    logger.info(
        "sc_research_summary",
        **summary.model_dump(exclude={"outcomes", "reasons"}),
        reasons=summary.reasons,
    )
    return summary


def _skipped(run_date: date, detail: str) -> JobRun:
    return journal_run(
        SC_RESEARCH_DATASET, run_date, rows_written=0, status=RunStatus.SKIPPED, detail=detail
    )


def _detail(summary: RunSummary) -> str:
    reasons = ",".join(f"{k}:{v}" for k, v in sorted(summary.reasons.items()))
    return f"{summary.line()} reasons=[{reasons}]"


async def run_research_job(  # noqa: PLR0913 - collaborators are injected, one per seam
    run_date: date,
    ctx: IngestContext,
    *,
    settings: Settings,
    search: SearchProvider | None = None,
    judge_client: LLMClient | None = None,
    second_client: LLMClient | None = None,
    root: Path | None = None,
    source: str = "all",
    limit: int | None = None,
    dry_run: bool = False,
) -> JobRun:
    assert_no_byok_scope()
    if not settings.supply_chain_research_enabled:
        return _skipped(run_date, "SUPPLY_CHAIN_RESEARCH_ENABLED is off")
    if not ctx.leader.is_leader():
        return _skipped(run_date, "not the leader - write skipped")
    if search is None or judge_client is None:
        blocker = missing_prerequisite(settings)
        if blocker is not None:
            return _skipped(run_date, blocker)
    try:
        summary = await execute_research(
            settings,
            ctx.store,
            today=run_date,
            source=source,
            limit=limit,
            dry_run=dry_run,
            is_leader=ctx.leader.is_leader,
            search=search,
            judge_client=judge_client,
            second_client=second_client,
            root=root,
        )
    except (RelationFileError, WriterError) as exc:
        logger.error("sc_research_invalid", error=str(exc))
        return journal_run(
            SC_RESEARCH_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=f"invalid relation file or write failure: {exc}",
        )
    detail = _detail(summary)
    if summary.rows_written and not dry_run:
        reindex = await job_sc_refdata_load(run_date, ctx)
        detail += f" reindex={reindex.status.value}"
    return journal_run(
        SC_RESEARCH_DATASET,
        run_date,
        rows_written=summary.rows_written,
        status=RunStatus.SUCCESS,
        detail=detail,
    )


async def job_sc_research(run_date: date, ctx: IngestContext) -> JobRun:
    """Weekly, leader-only, off unless SUPPLY_CHAIN_RESEARCH_ENABLED; capped by the run budget."""
    return await run_research_job(run_date, ctx, settings=get_settings())


__all__ = [
    "SC_RESEARCH_DATASET",
    "discovery_config",
    "execute_research",
    "job_sc_research",
    "missing_prerequisite",
    "prepare",
    "run_research_job",
]
