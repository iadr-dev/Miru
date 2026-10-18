"""Operator commands behind `python -m analytics.supply_chain discover|verify`.

Both print counts and claim ids only (a claim id is `kind:MARKET-symbol>MARKET-symbol`); page text
and model replies are never printed. `discover` lists the next targets. `verify --dry-run` runs the
whole pipeline (it does search and call the model, within the run caps) but writes nothing.
`verify` without `--dry-run` takes the ingest writer lease, writes the relation files and reindexes.
An operator running it by hand is not gated by SUPPLY_CHAIN_RESEARCH_ENABLED (that gates the job).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import anyio.to_thread

from analytics.supply_chain.refdata_io.loader import RelationFileError
from analytics.supply_chain.verification.job import (
    execute_research,
    missing_prerequisite,
    prepare,
)
from analytics.supply_chain.verification.models import RunSummary
from analytics.supply_chain.verification.writer import WriterError
from config.private_data import reference_dir
from config.settings import get_settings
from ingest.store import ParquetStore

LEASE_HELD_EXIT_CODE = 3


def format_summary(summary: RunSummary) -> str:
    lines = [summary.line()]
    if summary.reasons:
        lines.append(
            "reasons: " + ", ".join(f"{k}={v}" for k, v in sorted(summary.reasons.items()))
        )
    lines.extend(f"{claim_id} {kind}" for claim_id, kind in summary.outcomes.items())
    return "\n".join(lines) + "\n"


async def _discover(limit: int | None, source: str) -> int:
    settings = get_settings()
    today = datetime.now(UTC).date()
    store = ParquetStore(settings.parquet_root)
    root = reference_dir()
    targets, _, _ = await anyio.to_thread.run_sync(
        lambda: prepare(settings, store, root, today, source=source, limit=limit)
    )
    out = [f"targets={len(targets)}"]
    out.extend(f"{t.claim_id} {t.source.value}" for t in targets)
    sys.stdout.write("\n".join(out) + "\n")
    return 0


async def _verify(limit: int | None, source: str, *, dry_run: bool) -> int:
    settings = get_settings()
    blocker = missing_prerequisite(settings, need_second=settings.supply_chain_second_opinion)
    if blocker is not None:
        sys.stderr.write(f"cannot verify: {blocker}\n")
        return 1
    today = datetime.now(UTC).date()
    if dry_run:
        summary = await execute_research(
            settings,
            ParquetStore(settings.parquet_root),
            today=today,
            source=source,
            limit=limit,
            dry_run=True,
            is_leader=lambda: True,
            root=reference_dir(),
        )
        sys.stdout.write(format_summary(summary))
        return 0
    # Imported here: the daemon module imports the job module, and the dry run needs no database.
    from analytics.supply_chain.jobs import job_sc_refdata_load  # noqa: PLC0415
    from ingest.daemon import (  # noqa: PLC0415
        build_ingest_context_from_settings,
        close_ingest_context,
    )
    from ingest.leader import acquire_leadership  # noqa: PLC0415

    ctx = await build_ingest_context_from_settings(settings)
    try:
        if not await acquire_leadership(ctx.leader):
            sys.stderr.write(
                "another ingest process holds the writer lease; stop it or wait, then retry\n"
            )
            return LEASE_HELD_EXIT_CODE
        summary = await execute_research(
            settings,
            ctx.store,
            today=today,
            source=source,
            limit=limit,
            dry_run=False,
            is_leader=ctx.leader.is_leader,
            root=reference_dir(),
        )
        sys.stdout.write(format_summary(summary))
        if summary.rows_written:
            reindex = await job_sc_refdata_load(today, ctx)
            sys.stdout.write(f"reindex {reindex.status.value}\n")
        return 0
    finally:
        await close_ingest_context(ctx)


async def run_research_command(
    command: str, *, limit: int | None, dry_run: bool, source: str
) -> int:
    try:
        if command == "discover":
            return await _discover(limit, source)
        return await _verify(limit, source, dry_run=dry_run)
    except (RelationFileError, WriterError) as exc:
        sys.stderr.write(f"invalid file: {exc}\n")
        return 1


__all__ = ["format_summary", "run_research_command"]
