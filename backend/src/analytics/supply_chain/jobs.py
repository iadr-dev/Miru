"""Ingest job: rebuild the supply-chain index tables from the operator's private files.

Leader-only (the single writer), idempotent, and free of network I/O. Registered daily in
`ingest.daemon.SCHEDULED_JOBS` and run once in the dev startup catch-up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as date_

import structlog

from analytics.supply_chain.positions.overrides import OverrideFileError, load_overrides
from analytics.supply_chain.positions.service import compute_positions, summarize
from analytics.supply_chain.refdata_io.loader import RelationFileError, load_reference_relations
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from analytics.themes.store import SqlAlchemyThemeStore
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run

logger = structlog.get_logger(__name__)

SC_REFDATA_DATASET = "sc_refdata_load"
SC_POSITIONS_DATASET = "sc_positions_refresh"


async def job_sc_refdata_load(run_date: date_, ctx: IngestContext) -> JobRun:
    if not ctx.leader.is_leader():
        return journal_run(
            SC_REFDATA_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader - write skipped",
        )
    if ctx.session_factory is None:
        return journal_run(
            SC_REFDATA_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="no database session factory wired",
        )
    async with ctx.session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        try:
            result = await load_reference_relations(store, today=run_date)
        except RelationFileError as exc:
            await session.rollback()
            logger.error("sc_refdata_load_invalid", error=str(exc))
            return journal_run(
                SC_REFDATA_DATASET,
                run_date,
                rows_written=0,
                status=RunStatus.FAILED,
                detail=f"invalid relation file: {exc}",
            )
        await session.commit()
    if result.skipped:
        return journal_run(
            SC_REFDATA_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="no relation files found",
        )
    return journal_run(
        SC_REFDATA_DATASET,
        run_date,
        rows_written=result.rows,
        status=RunStatus.SUCCESS,
        detail=(
            f"{result.files} files, {result.created} created, {result.updated} updated, "
            f"{result.deleted} deleted, {result.duplicates} duplicates skipped"
        ),
    )


async def job_sc_positions_refresh(run_date: date_, ctx: IngestContext) -> JobRun:
    """Recompute every chain position and rewrite `sc_positions` (derived data, replaced whole).

    Runs after `sc_refdata_load` so it sees the current relations. With no industry memberships
    there is nothing to classify: journaled as skipped, and the existing rows are left alone."""
    if not ctx.leader.is_leader():
        return journal_run(
            SC_POSITIONS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader - write skipped",
        )
    if ctx.session_factory is None:
        return journal_run(
            SC_POSITIONS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="no database session factory wired",
        )
    try:
        overrides = load_overrides()
    except OverrideFileError as exc:
        logger.error("sc_positions_invalid_overrides", error=str(exc))
        return journal_run(
            SC_POSITIONS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=f"invalid overrides file: {exc}",
        )
    async with ctx.session_factory() as session:
        positions, memberships = await compute_positions(
            SqlAlchemyThemeStore(session), SqlAlchemySupplyChainStore(session), overrides
        )
        if memberships == 0:
            return journal_run(
                SC_POSITIONS_DATASET,
                run_date,
                rows_written=0,
                status=RunStatus.SKIPPED,
                detail="no industry memberships to classify",
            )
        written = await SqlAlchemySupplyChainStore(session).replace_positions(
            positions, computed_at=datetime.now(UTC)
        )
        await session.commit()
    counts = summarize(positions)
    return journal_run(
        SC_POSITIONS_DATASET,
        run_date,
        rows_written=written,
        status=RunStatus.SUCCESS,
        detail=f"{memberships} memberships; by position {counts['position']}; "
        f"by method {counts['method']}",
    )


__all__ = [
    "SC_POSITIONS_DATASET",
    "SC_REFDATA_DATASET",
    "job_sc_positions_refresh",
    "job_sc_refdata_load",
]
