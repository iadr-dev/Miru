"""The weekly `sc_theme_candidates` ingest job and the `discover --themes` operator command.

Both spend nothing (no search, no LLM, no network), so neither is gated by the research flag. The
job is leader-only and journals SKIPPED, with a reason, when the reference files are absent.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path

import anyio.to_thread
import structlog
from sqlalchemy.exc import SQLAlchemyError

from analytics.supply_chain.theme_discovery.industry import load_industry_groups
from analytics.supply_chain.theme_discovery.io import (
    ThemeMiningError,
    format_table,
    missing_inputs,
    run_theme_mining,
)
from analytics.supply_chain.theme_discovery.mining import (
    DEFAULT_LIMIT,
    DEFAULT_MAX_COMPANY_SHARE,
    DEFAULT_MIN_COMPANIES,
    DEFAULT_OVERLAP_THRESHOLD,
)
from analytics.themes.store import SqlAlchemyThemeStore
from config.private_data import reference_dir
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run

logger = structlog.get_logger(__name__)

SC_THEME_CANDIDATES_DATASET = "sc_theme_candidates"


def _journal(run_date: date, status: RunStatus, detail: str, rows: int = 0) -> JobRun:
    return journal_run(
        SC_THEME_CANDIDATES_DATASET, run_date, rows_written=rows, status=status, detail=detail
    )


async def _industry_groups(ctx: IngestContext) -> dict[str, frozenset[str]] | None:
    """Industry-chain membership from the themes table, or None (overlap then uses concept
    themes only) when no database is wired or it cannot be read."""
    if ctx.session_factory is None:
        return None
    try:
        async with ctx.session_factory() as session:
            return await load_industry_groups(SqlAlchemyThemeStore(session))
    except SQLAlchemyError as exc:
        logger.warning("sc_theme_candidates_industry_groups_unavailable", error=str(exc))
        return None


async def run_theme_candidates_job(
    run_date: date, ctx: IngestContext, *, root: Path | None = None
) -> JobRun:
    if not ctx.leader.is_leader():
        return _journal(run_date, RunStatus.SKIPPED, "not the leader - write skipped")
    base = root or reference_dir()
    blocker = missing_inputs(base)
    if blocker is not None:
        return _journal(run_date, RunStatus.SKIPPED, blocker)
    groups = await _industry_groups(ctx)
    try:
        result = await anyio.to_thread.run_sync(
            lambda: run_theme_mining(base, today=run_date, industry_groups=groups)
        )
    except (ThemeMiningError, OSError) as exc:
        logger.error("sc_theme_candidates_failed", error=str(exc))
        return _journal(run_date, RunStatus.FAILED, f"invalid file or write failure: {exc}")
    counts: dict[str, int] = {}
    for c in result.candidates:
        counts[c.trend.value] = counts.get(c.trend.value, 0) + 1
    detail = f"candidates={len(result.candidates)} " + " ".join(
        f"{k}={v}" for k, v in sorted(counts.items())
    )
    return _journal(run_date, RunStatus.SUCCESS, detail.strip(), len(result.candidates))


async def job_sc_theme_candidates(run_date: date, ctx: IngestContext) -> JobRun:
    """Weekly, leader-only: mine concept-theme candidates into the private candidates file."""
    return await run_theme_candidates_job(run_date, ctx)


def run_themes_command(  # noqa: PLR0913 - keyword-only CLI knobs
    *,
    min_companies: int | None,
    limit: int | None,
    dry_run: bool,
    max_share: float | None = None,
    overlap_threshold: float | None = None,
    root: Path | None = None,
    industry_groups: Mapping[str, frozenset[str]] | None = None,
) -> int:
    """`discover --themes`: prints the table (symbols only, no company names)."""
    base = root or reference_dir()
    blocker = missing_inputs(base)
    if blocker is not None:
        sys.stderr.write(f"cannot mine themes: {blocker}\n")
        return 1
    try:
        result = run_theme_mining(
            base,
            today=datetime.now(UTC).date(),
            min_companies=min_companies if min_companies is not None else DEFAULT_MIN_COMPANIES,
            limit=limit if limit is not None else DEFAULT_LIMIT,
            max_company_share=max_share if max_share is not None else DEFAULT_MAX_COMPANY_SHARE,
            overlap_threshold=(
                overlap_threshold if overlap_threshold is not None else DEFAULT_OVERLAP_THRESHOLD
            ),
            dry_run=dry_run,
            industry_groups=industry_groups,
        )
    except ThemeMiningError as exc:
        sys.stderr.write(f"invalid file: {exc}\n")
        return 1
    sys.stdout.write(format_table(result.candidates))
    sys.stdout.write(f"skipped={len(result.skipped)}\n")
    sys.stdout.write("dry run: nothing written\n" if dry_run else "written\n")
    return 0


__all__ = [
    "SC_THEME_CANDIDATES_DATASET",
    "job_sc_theme_candidates",
    "run_theme_candidates_job",
    "run_themes_command",
]
