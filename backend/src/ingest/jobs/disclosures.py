"""MOPS disclosure jobs: material announcements and insider transfer declarations."""

from __future__ import annotations

from datetime import date as date_
from typing import cast

import structlog

from ingest.context import IngestContext
from ingest.jobs.whole_market import fetch_error_status as _fetch_error_status
from ingest.jobs.whole_market import validate_rows_against as _validate_rows_against
from ingest.journal import JobRun, RunStatus, journal_run
from schemas.capabilities import (
    InsiderTransferDeclarationRow,
    MaterialAnnouncementRow,
)
from schemas.enums import Capability
from sources.mops import (
    fetch_whole_market_insider_transfer_declarations,
    fetch_whole_market_material_announcements,
)

logger = structlog.get_logger(__name__)


MATERIAL_ANNOUNCEMENTS_DATASET = "material_announcements"


async def job_material_announcements(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily` — MOPS 重大訊息 (material announcement) whole-market feed
    (`sources/mops.py`), cron **daily, off-peak** (see `ingest/daemon.py`'s `SCHEDULED_JOBS`).

    Not FinMind-sourced — `fetch_whole_market_material_announcements` is `sources/mops.py`'s own
    dedicated whole-market fetch function, the same ingest-layer whole-market carve-out
    `job_etf_nav` uses for its own non-FinMind upstream.

    **Forward-only — no backfill is possible.** MOPS's own upstream has no date parameter; every
    call returns only today's live dump (`sources/mops.py::_ANNOUNCEMENTS_CAVEAT`: "not a
    historical archive; announcements roll off the upstream dataset over time"). This job's
    first successful run seeds day 1 of history; there is no historical range to walk backward
    over the way `run_backfill_cli` does for FinMind datasets. See `ingest/__main__.py`'s
    `_tw_gap_fill` for why this job is ALSO called directly from the startup gap-fill path, not
    only from its own daily cron slot — a day missed because the ingest container was down at
    the cron fire time is otherwise permanently lost, since there is nothing upstream to recover
    it from later."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            MATERIAL_ANNOUNCEMENTS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            MATERIAL_ANNOUNCEMENTS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    result = await fetch_whole_market_material_announcements(tls_policy=ctx.tls_policy)
    if _fetch_error_status(result) is RunStatus.FAILED:
        return journal_run(
            MATERIAL_ANNOUNCEMENTS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=result.health.detail,
        )

    raw_rows_obj = result.payload.get("rows", [])
    raw_rows: list[dict[str, object]] = (
        cast("list[dict[str, object]]", raw_rows_obj) if isinstance(raw_rows_obj, list) else []
    )
    rows = _validate_rows_against(
        MaterialAnnouncementRow, MATERIAL_ANNOUNCEMENTS_DATASET, raw_rows, run_date
    )
    rows_written = ctx.store.write_partition(
        MATERIAL_ANNOUNCEMENTS_DATASET, ctx.market.value, run_date, rows
    )
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.CORPORATE_ACTIONS,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    return journal_run(
        MATERIAL_ANNOUNCEMENTS_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=result.health.detail,
    )


INSIDER_TRANSFER_DECLARATION_DATASET = "insider_transfer_declaration"


async def job_insider_transfer_declaration(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily` — MOPS 內部人股權轉讓事前申報 (insider advance-transfer-plan declaration)
    whole-market feed (`sources/mops.py`, `t187ap12_L` only — see
    `InsiderTransferDeclarationRow`'s own docstring for why `t187ap13_L` is out of scope), cron
    **daily, off-peak** (see `ingest/daemon.py`'s `SCHEDULED_JOBS`). Same forward-only-no-backfill
    and startup-gap-fill reasoning as `job_material_announcements` above — see its docstring."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            INSIDER_TRANSFER_DECLARATION_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            INSIDER_TRANSFER_DECLARATION_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    result = await fetch_whole_market_insider_transfer_declarations(tls_policy=ctx.tls_policy)
    if _fetch_error_status(result) is RunStatus.FAILED:
        return journal_run(
            INSIDER_TRANSFER_DECLARATION_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=result.health.detail,
        )

    raw_rows_obj = result.payload.get("rows", [])
    raw_rows: list[dict[str, object]] = (
        cast("list[dict[str, object]]", raw_rows_obj) if isinstance(raw_rows_obj, list) else []
    )
    rows = _validate_rows_against(
        InsiderTransferDeclarationRow, INSIDER_TRANSFER_DECLARATION_DATASET, raw_rows, run_date
    )
    rows_written = ctx.store.write_partition(
        INSIDER_TRANSFER_DECLARATION_DATASET, ctx.market.value, run_date, rows
    )
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.INSIDER_TRANSFER_DECLARATION,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    return journal_run(
        INSIDER_TRANSFER_DECLARATION_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=result.health.detail,
    )
