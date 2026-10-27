"""Attention-stock and disposition-event jobs behind the disposition board."""

from __future__ import annotations

from datetime import date as date_
from typing import cast

import structlog

from ingest.context import IngestContext
from ingest.event_snapshots import EXCHANGES, attention_snapshot, newest_by_exchange
from ingest.jobs.whole_market import fetch_error_status as _fetch_error_status
from ingest.jobs.whole_market import validate_rows_against as _validate_rows_against
from ingest.journal import JobRun, RunStatus, journal_run
from ingest.rows import without_path_columns
from schemas.capabilities import (
    AttentionStockRow,
    DispositionEventRow,
)
from schemas.enums import Capability
from sources.exchange_event_history import ExchangeEventHistoryAdapter
from sources.exchange_whole_market._common import ExchangeFetchError
from sources.tpex_openapi import fetch_whole_market_disposition_events_tpex
from sources.twse_openapi import fetch_whole_market_disposition_events

logger = structlog.get_logger(__name__)


ATTENTION_STOCKS_DATASET = "attention_stocks"


def _failed_kinds(twse_failed: bool, tpex_failed: bool) -> set[str]:
    return {kind for kind, down in (("twse", twse_failed), ("tpex", tpex_failed)) if down}


def _settled_rows(
    ctx: IngestContext,
    dataset: str,
    run_date: date_,
    rows: list[dict[str, object]],
    failed: set[str],
) -> list[dict[str, object]] | None:
    """The rows to write for the day, or `None` to leave the stored partition alone.

    One exchange failing (`failed` holds "twse" / "tpex") must not wipe its earlier rows from the
    day: they are carried forward (`security_type` says which exchange a row came from). A result
    with no rows at all never replaces a partition that already has some: a glitchy empty answer
    is not a day with nothing on the list."""
    if failed:
        stored = without_path_columns(
            ctx.store.read_partition(dataset, ctx.market.value, run_date),
            keep=frozenset({"market"}),
        )
        rows = [*rows, *(r for r in stored if r.get("security_type") in failed)]
    if not rows and ctx.store.has_rows(dataset, ctx.market.value, run_date):
        return None
    return rows


def _kept_run(dataset: str, run_date: date_) -> JobRun:
    return journal_run(
        dataset,
        run_date,
        rows_written=0,
        status=RunStatus.SKIPPED,
        detail="the feed returned no rows; kept the day's earlier non-empty snapshot",
    )


def new_event_history_adapter() -> ExchangeEventHistoryAdapter:
    """The throttled client pair behind one attention run (one request per exchange)."""
    return ExchangeEventHistoryAdapter()


async def job_attention_stocks(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily` — 注意股 (attention-stock) whole-market snapshot of `run_date`.

    Both exchanges come from their date-range history reports (`sources/exchange_event_history`,
    the reports `ingest/event_history.py` rebuilds the past from), not from the current-day
    OpenAPI dumps: TWSE's `announcement/notice` dump answers only its empty placeholder, so a job
    reading it stored no listed-market flag at all. The day's rows are cut with the same rule the
    rebuild uses (`attention_snapshot`: the flags announced on `run_date` and on the trading day
    before it), so the live and the rebuilt snapshots are one series.

    **Never half a market.** If either exchange cannot be read (`ExchangeFetchError`) or has not
    posted `run_date` yet, NOTHING is written and the run is FAILED / SKIPPED: the day stays a gap
    that the next run (also called from `ingest/__main__.py`'s startup gap-fill) or the
    `--event-history` rebuild fills, instead of becoming a stored snapshot of one exchange.

    Cron **daily, off-peak** (see `ingest/daemon.py`'s `SCHEDULED_JOBS`)."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            ATTENTION_STOCKS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            ATTENTION_STOCKS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    previous = ctx.calendar.previous_trading_day(run_date)
    try:
        history = await new_event_history_adapter().fetch_attention(
            previous, run_date, tls_policy=ctx.tls_policy
        )
    except ExchangeFetchError as exc:
        logger.warning("attention_stocks_fetch_failed", day=run_date.isoformat(), reason=exc.detail)
        return journal_run(
            ATTENTION_STOCKS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=f"nothing written, the day stays a gap: {exc.detail}",
        )
    valid = _validate_rows_against(
        AttentionStockRow, ATTENTION_STOCKS_DATASET, history.rows, run_date
    )
    reached = newest_by_exchange(valid, "flagged_date")
    behind = sorted(kind for kind in EXCHANGES if reached.get(kind, date_.min) < run_date)
    if behind:
        return journal_run(
            ATTENTION_STOCKS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=(
                f"not posted yet by {'/'.join(behind)}, nothing written, the day stays a gap "
                "for the next run"
            ),
        )
    rows = attention_snapshot(valid, run_date, previous)
    rows_written = ctx.store.write_partition(
        ATTENTION_STOCKS_DATASET, ctx.market.value, run_date, rows
    )
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.ATTENTION_STOCKS,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    return journal_run(
        ATTENTION_STOCKS_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail="ok",
    )


DISPOSITION_EVENTS_DATASET = "disposition_events"


async def job_disposition_events(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily` — 處置 (disposition) whole-market feed, TWSE `announcement/punish` plus
    TPEX `tpex_disposal_information`. Same shape, same forward-only/gap-fill reasoning as
    `job_attention_stocks` above — see its docstring."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            DISPOSITION_EVENTS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            DISPOSITION_EVENTS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    twse_result = await fetch_whole_market_disposition_events(tls_policy=ctx.tls_policy)
    tpex_result = await fetch_whole_market_disposition_events_tpex(tls_policy=ctx.tls_policy)
    twse_failed = _fetch_error_status(twse_result) is RunStatus.FAILED
    tpex_failed = _fetch_error_status(tpex_result) is RunStatus.FAILED
    if twse_failed and tpex_failed:
        return journal_run(
            DISPOSITION_EVENTS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=f"twse={twse_result.health.detail}; tpex={tpex_result.health.detail}",
        )

    raw_rows: list[dict[str, object]] = []
    for result, failed in ((twse_result, twse_failed), (tpex_result, tpex_failed)):
        if failed:
            continue
        rows_obj = result.payload.get("rows", [])
        if isinstance(rows_obj, list):
            raw_rows.extend(cast("list[dict[str, object]]", rows_obj))

    rows = _validate_rows_against(
        DispositionEventRow, DISPOSITION_EVENTS_DATASET, raw_rows, run_date
    )
    settled = _settled_rows(
        ctx, DISPOSITION_EVENTS_DATASET, run_date, rows, _failed_kinds(twse_failed, tpex_failed)
    )
    if settled is None:
        return _kept_run(DISPOSITION_EVENTS_DATASET, run_date)
    rows_written = ctx.store.write_partition(
        DISPOSITION_EVENTS_DATASET, ctx.market.value, run_date, settled
    )
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.DISPOSITION_EVENTS,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    status = RunStatus.PARTIAL if (twse_failed or tpex_failed) else RunStatus.SUCCESS
    detail = (
        f"twse={twse_result.health.detail}; tpex={tpex_result.health.detail}"
        if (twse_failed or tpex_failed)
        else "ok"
    )
    return journal_run(
        DISPOSITION_EVENTS_DATASET,
        run_date,
        rows_written=rows_written,
        status=status,
        detail=detail,
    )
