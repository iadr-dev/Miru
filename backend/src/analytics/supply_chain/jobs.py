"""Ingest job: rebuild the supply-chain index tables from the operator's private files.

Leader-only (the single writer), idempotent, and free of network I/O. Registered daily in
`ingest.daemon.SCHEDULED_JOBS` and run once in the dev startup catch-up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as date_

import structlog

from analytics.supply_chain.entities_io.loader import EntityFileError, load_entities
from analytics.supply_chain.positions.overrides import OverrideFileError, load_overrides
from analytics.supply_chain.positions.service import compute_positions, summarize
from analytics.supply_chain.refdata_io.loader import RelationFileError, load_reference_relations
from analytics.supply_chain.store.concept_protocol import ConceptCollisionError
from analytics.supply_chain.store.concept_store import SqlAlchemyConceptStore
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from analytics.supply_chain.themes_io.loader import ConceptFileError, load_concept_themes
from analytics.themes.store import SqlAlchemyThemeStore
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run

_NO_SESSION_FACTORY_DETAIL = "no database session factory wired"

_NOT_LEADER_DETAIL = "not the leader - write skipped"

logger = structlog.get_logger(__name__)

SC_REFDATA_DATASET = "sc_refdata_load"
SC_POSITIONS_DATASET = "sc_positions_refresh"
SC_THEMES_ENTITIES_DATASET = "sc_themes_entities_load"


async def job_sc_refdata_load(run_date: date_, ctx: IngestContext) -> JobRun:
    if not ctx.leader.is_leader():
        return journal_run(
            SC_REFDATA_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=_NOT_LEADER_DETAIL,
        )
    if ctx.session_factory is None:
        return journal_run(
            SC_REFDATA_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=_NO_SESSION_FACTORY_DETAIL,
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


async def job_sc_themes_entities_load(run_date: date_, ctx: IngestContext) -> JobRun:
    """Rebuild the concept-theme and entity tables (and the concept themes' mirror in the generic
    theme tables) from the operator's private files. The two loads are independent: a bad file in
    one is journaled as a failure but never blocks, or rolls back, the other."""
    dataset = SC_THEMES_ENTITIES_DATASET
    if not ctx.leader.is_leader():
        return journal_run(
            dataset,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=_NOT_LEADER_DETAIL,
        )
    if ctx.session_factory is None:
        return journal_run(
            dataset,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=_NO_SESSION_FACTORY_DETAIL,
        )
    rows = 0
    parts: list[str] = []
    failures: list[str] = []
    skipped = 0
    async with ctx.session_factory() as session:
        store = SqlAlchemyConceptStore(session)
        try:
            themes = await load_concept_themes(store)
        except (ConceptFileError, ConceptCollisionError) as exc:
            await session.rollback()
            logger.error("sc_themes_load_invalid", error=str(exc))
            failures.append(f"concept themes: {exc}")
        else:
            await session.commit()
            if themes.skipped:
                skipped += 1
            else:
                rows += themes.members
                parts.append(
                    f"{themes.themes} themes/{themes.members} members "
                    f"({themes.created} created, {themes.deleted} deleted)"
                )
        try:
            entities = await load_entities(store)
        except EntityFileError as exc:
            await session.rollback()
            logger.error("sc_entities_load_invalid", error=str(exc))
            failures.append(f"entities: {exc}")
        else:
            await session.commit()
            if entities.skipped:
                skipped += 1
            else:
                rows += entities.entities + entities.mentions
                parts.append(
                    f"{entities.entities} entities/{entities.mentions} mentions "
                    f"({entities.dropped} dropped, {entities.deleted_mentions} deleted)"
                )
    if failures:
        return journal_run(
            dataset,
            run_date,
            rows_written=rows,
            status=RunStatus.FAILED,
            detail="invalid file: " + "; ".join(failures + parts),
        )
    if skipped == 2:  # noqa: PLR2004 - both loads found no files
        return journal_run(
            dataset,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="no concept-theme or entity files found",
        )
    return journal_run(
        dataset, run_date, rows_written=rows, status=RunStatus.SUCCESS, detail=", ".join(parts)
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
            detail=_NOT_LEADER_DETAIL,
        )
    if ctx.session_factory is None:
        return journal_run(
            SC_POSITIONS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=_NO_SESSION_FACTORY_DETAIL,
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
    "SC_THEMES_ENTITIES_DATASET",
    "job_sc_positions_refresh",
    "job_sc_refdata_load",
    "job_sc_themes_entities_load",
]
