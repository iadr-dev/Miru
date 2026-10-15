"""策略盯盤 intraday slot: one whole-market `taiwan_stock_tick_snapshot` call every 10 minutes from
09:00 to 13:30, compared against the morning `strategy_watchlist`.

Ingest owns this because it is a whole-market FinMind read (the platform-ingest carve-out; only
ingest may call `fetch_whole_market`). The scheduler never polls: it reads the `strategy_hits`
partition this writes, on the `data.updated` signal published after each slot lands. The snapshot
is FinMind Sponsor data, so the job needs a Sponsor token; a refusal is a journaled failure.
"""

from __future__ import annotations

from datetime import date as date_
from datetime import datetime
from datetime import time as time_
from typing import cast
from zoneinfo import ZoneInfo

import structlog

from analytics.strategy_watch.batch import STRATEGY_WATCHLIST_DATASET
from analytics.strategy_watch.intraday import evaluate_slot, slot_of
from ingest.context import IngestContext
from ingest.jobs.whole_market import fetch_error_status
from ingest.journal import JobRun, RunStatus, journal_run
from schemas.enums import Capability

logger = structlog.get_logger(__name__)

STRATEGY_HITS_DATASET = "strategy_hits"
_TAIPEI = ZoneInfo("Asia/Taipei")


def _skip(run_date: date_, detail: str) -> JobRun:
    return journal_run(
        STRATEGY_HITS_DATASET, run_date, rows_written=0, status=RunStatus.SKIPPED, detail=detail
    )


def _gate(run_date: date_, ctx: IngestContext) -> str | None:
    """Why this slot must not run, or `None` when it may."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return "non-trading day (calendar-gated)"
    if not ctx.leader.is_leader():
        return "not the leader — write skipped"
    if not ctx.store.has_partition(STRATEGY_WATCHLIST_DATASET, ctx.market.value, run_date):
        return "no watchlist for today (the 08:00 screen did not publish)"
    return None


def _rows_for(payload: dict[str, object], day: str) -> list[dict[str, object]]:
    """The snapshot rows dated `day`. A snapshot from another day means the market has not opened
    (or the feed is stale): every comparison against today's levels would be wrong."""
    raw = payload.get("rows")
    if not isinstance(raw, list):
        return []
    rows = [cast("dict[str, object]", r) for r in cast("list[object]", raw) if isinstance(r, dict)]
    return [r for r in rows if str(r.get("trade_date", ""))[:10] == day]


async def run_strategy_slot(run_date: date_, ctx: IngestContext, *, at: time_) -> JobRun:
    """Check the slot `at` falls in and append its hits to today's `strategy_hits` partition."""
    reason = _gate(run_date, ctx)
    if reason is not None:
        return _skip(run_date, reason)
    market = ctx.market.value
    watchlist = ctx.store.read_partition(STRATEGY_WATCHLIST_DATASET, market, run_date)
    if not watchlist:
        return _skip(run_date, "today's watchlist is empty")

    result = await ctx.finmind.fetch_whole_market(
        Capability.TICK_SNAPSHOT, tls_policy=ctx.tls_policy
    )
    status = fetch_error_status(result)
    if status is not RunStatus.SUCCESS:
        return journal_run(
            STRATEGY_HITS_DATASET,
            run_date,
            rows_written=0,
            status=status,
            detail=f"snapshot unavailable: {result.health.detail}",
        )
    today = run_date.isoformat()
    snapshot = _rows_for(dict(result.payload), today)
    if not snapshot:
        return _skip(run_date, "the snapshot has no quotes for today")

    slot = slot_of(at)
    slot_label = slot.strftime("%H:%M")
    existing = (
        ctx.store.read_partition(STRATEGY_HITS_DATASET, market, run_date)
        if ctx.store.has_partition(STRATEGY_HITS_DATASET, market, run_date)
        else []
    )
    earlier = [r for r in existing if str(r.get("slot", "")) < slot_label]
    previous_slot = max((str(r["slot"]) for r in earlier), default=None)
    previously_met = frozenset(
        (str(r["symbol"]), str(r["strategy"]))
        for r in earlier
        if r.get("slot") == previous_slot and r.get("all_met") is True
    )
    hits = evaluate_slot(watchlist, snapshot, slot=slot, previously_met=previously_met)
    # Re-running a slot replaces it; every other slot of the day is kept.
    kept = [r for r in existing if r.get("slot") != slot_label]
    rows = kept + [hit.to_row(today) for hit in hits]
    ctx.store.write_partition(STRATEGY_HITS_DATASET, market, run_date, rows)
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.TICK_SNAPSHOT,
        market=ctx.market,
        as_of=f"{today}T{slot_label}",
        scope="strategy_watch",
    )
    met = sum(1 for h in hits if h.all_met)
    alertable = sum(1 for h in hits if h.alertable)
    logger.info(
        "strategy_slot_checked",
        run_date=today,
        slot=slot_label,
        checked=len(hits),
        all_met=met,
        alertable=alertable,
    )
    return journal_run(
        STRATEGY_HITS_DATASET,
        run_date,
        rows_written=len(hits),
        status=RunStatus.SUCCESS,
        detail=f"slot {slot_label}: {len(hits)} checked, {met} all met, {alertable} alertable",
    )


async def job_strategy_intraday(run_date: date_, ctx: IngestContext) -> JobRun:
    return await run_strategy_slot(run_date, ctx, at=datetime.now(_TAIPEI).time())


__all__ = ["STRATEGY_HITS_DATASET", "job_strategy_intraday", "run_strategy_slot"]
