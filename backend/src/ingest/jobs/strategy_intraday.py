"""策略盯盤 intraday slot: one quote read every 10 minutes from 09:00 to 13:30, compared against
the morning `strategy_watchlist`.

The quotes come from the free TWSE MIS feed first (`ctx.intraday_quotes`: just the watchlist's
codes plus the index, ~5 s delayed), and only when it cannot be read from FinMind's whole-market
`taiwan_stock_tick_snapshot`, a Sponsor dataset that needs a Sponsor token (a refusal is a
journaled failure). Ingest owns this because it is a platform read, not a per-user one. The
scheduler never polls: it reads the `strategy_hits` partition this writes, on the `data.updated`
signal published after each slot lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_
from datetime import datetime
from datetime import time as time_
from typing import cast
from zoneinfo import ZoneInfo

import structlog

from analytics.coerce import as_float
from analytics.strategy_watch.batch import STRATEGY_WATCHLIST_DATASET
from analytics.strategy_watch.intraday import (
    CLOSE_SLOT,
    Hit,
    evaluate_close,
    evaluate_slot,
    slot_of,
)
from ingest.context import IngestContext
from ingest.jobs.whole_market import fetch_error_status
from ingest.journal import JobRun, RunStatus, journal_run
from ingest.rows import without_path_columns
from schemas.enums import Capability
from sources.twse_mis import MisQuote

logger = structlog.get_logger(__name__)

STRATEGY_HITS_DATASET = "strategy_hits"
PRICE_EOD_DATASET = "TaiwanStockPrice"
_TAIPEI = ZoneInfo("Asia/Taipei")
_SESSION_OPEN = time_(9, 0)
_SESSION_MINUTES = 270  # 09:00-13:30
_FREE_SOURCE = "twse_mis"


@dataclass(frozen=True)
class _Snapshot:
    """The quotes one slot compares. `whole_market` is false for the watchlist-only free feed,
    whose market filter is the index change instead of the median of every stock."""

    rows: list[dict[str, object]]
    market_pct: float | None
    whole_market: bool
    source: str


@dataclass(frozen=True)
class _FreeRead:
    snapshot: _Snapshot | None
    use_fallback: bool  # the feed could not be read: ask FinMind
    reason: str


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


def _volume_ratio(quote: MisQuote, average_20d: float | None) -> float | None:
    """量比: the day's volume per elapsed minute against the 20-session average per session
    minute. The exchange reports cumulative volume only, so this is the standard minute-rate
    comparison; it needs the average from the morning watchlist."""
    if not average_20d or average_20d <= 0 or not quote.observed_at:
        return None
    try:
        seen = time_.fromisoformat(quote.observed_at)
    except ValueError:
        return None
    elapsed = (seen.hour - _SESSION_OPEN.hour) * 60 + seen.minute - _SESSION_OPEN.minute
    elapsed = min(max(elapsed, 1), _SESSION_MINUTES)
    return (quote.volume_shares / elapsed) / (average_20d / _SESSION_MINUTES)


def _change_pct(quote: MisQuote) -> float | None:
    if quote.last is None or quote.prev_close is None:
        return None
    return (quote.last - quote.prev_close) / quote.prev_close * 100


def _quote_row(quote: MisQuote, average_20d: float | None) -> dict[str, object]:
    return {
        "symbol": quote.symbol,
        "trade_date": quote.trade_date,
        "close": quote.last,
        "change_rate": _change_pct(quote),
        "open": quote.open,
        "volume_ratio": _volume_ratio(quote, average_20d),
        # The exchange feed carries no turnover, so the day's average price is unknown and the
        # `above_average_price` guard stays open for these rows.
        "average_price": None,
    }


async def _free_snapshot(
    ctx: IngestContext, watchlist: list[dict[str, object]], day: str
) -> _FreeRead:
    """The watchlist's quotes from the free exchange feed. `use_fallback` is true only when the
    feed could not be read; a readable feed with no trade yet today is an honest empty slot."""
    if ctx.intraday_quotes is None:
        return _FreeRead(None, True, "free exchange feed not configured")
    averages: dict[str, float] = {}
    for row in watchlist:
        average = as_float(row.get("avg_volume_20d"))
        if isinstance(row.get("symbol"), str) and average:
            averages[str(row["symbol"])] = average
    symbols = sorted({str(r["symbol"]) for r in watchlist if isinstance(r.get("symbol"), str)})
    result = await ctx.intraday_quotes(symbols, tls_policy=ctx.tls_policy)
    if not result.quotes:
        return _FreeRead(None, True, result.health.detail)
    traded = [q for q in result.quotes if q.trade_date == day and q.last is not None]
    if not traded:
        return _FreeRead(None, False, "the exchange feed reports no trade for today yet")
    index = result.index
    market_pct = _change_pct(index) if index is not None and index.trade_date == day else None
    rows = [_quote_row(q, averages.get(q.symbol)) for q in traded]
    return _FreeRead(_Snapshot(rows, market_pct, False, _FREE_SOURCE), False, "ok")


async def run_strategy_slot(run_date: date_, ctx: IngestContext, *, at: time_) -> JobRun:
    """Check the slot `at` falls in and append its hits to today's `strategy_hits` partition."""
    reason = _gate(run_date, ctx)
    if reason is not None:
        return _skip(run_date, reason)
    market = ctx.market.value
    watchlist = ctx.store.read_partition(STRATEGY_WATCHLIST_DATASET, market, run_date)
    if not watchlist:
        return _skip(run_date, "today's watchlist is empty")

    free = await _free_snapshot(ctx, watchlist, run_date.isoformat())
    snapshot = free.snapshot
    if snapshot is None and not free.use_fallback:
        return _skip(run_date, free.reason)
    if snapshot is None:
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
                detail=f"snapshot unavailable: {result.health.detail} (free feed: {free.reason})",
            )
        rows = _rows_for(dict(result.payload), run_date.isoformat())
        if not rows:
            return _skip(run_date, "the snapshot has no quotes for today")
        snapshot = _Snapshot(rows, None, True, "finmind")
    today = run_date.isoformat()
    slot = slot_of(at)
    slot_label = slot.strftime("%H:%M")
    existing = (
        ctx.store.read_partition(STRATEGY_HITS_DATASET, market, run_date)
        if ctx.store.has_partition(STRATEGY_HITS_DATASET, market, run_date)
        else []
    )
    earlier = [
        r for r in existing if r.get("slot") != CLOSE_SLOT and str(r.get("slot", "")) < slot_label
    ]
    previous_slot = max((str(r["slot"]) for r in earlier), default=None)
    previously_met = frozenset(
        (str(r["symbol"]), str(r["strategy"]))
        for r in earlier
        if r.get("slot") == previous_slot and r.get("all_met") is True
    )
    hits = evaluate_slot(
        watchlist,
        snapshot.rows,
        slot=slot,
        previously_met=previously_met,
        market_pct="median" if snapshot.whole_market else snapshot.market_pct,
    )
    _store_slot(ctx, run_date, slot_label, hits)
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
        detail=(
            f"slot {slot_label}: {len(hits)} checked, {met} all met, {alertable} alertable "
            f"(via {snapshot.source})"
        ),
    )


def _store_slot(ctx: IngestContext, run_date: date_, slot: str, hits: list[Hit]) -> None:
    """Append one slot's hits to the day's partition; re-running a slot replaces it."""
    market = ctx.market.value
    existing = (
        ctx.store.read_partition(STRATEGY_HITS_DATASET, market, run_date)
        if ctx.store.has_partition(STRATEGY_HITS_DATASET, market, run_date)
        else []
    )
    new_rows = [hit.to_row(run_date.isoformat()) for hit in hits]
    keep: frozenset[str] = (
        frozenset({"market"}) if any("market" in r for r in new_rows) else frozenset()
    )
    kept = [r for r in without_path_columns(existing, keep=keep) if r.get("slot") != slot]
    rows = kept + new_rows
    ctx.store.write_partition(STRATEGY_HITS_DATASET, market, run_date, rows)


async def job_strategy_close_check(run_date: date_, ctx: IngestContext) -> JobRun:
    """The close confirmation: today's EOD bar against this morning's breakout levels. Needs
    today's `TaiwanStockPrice` partition (the 17:40 pull); without it the run is a journaled skip.
    No FinMind call of its own."""
    reason = _gate(run_date, ctx)
    if reason is not None:
        return _skip(run_date, reason)
    market = ctx.market.value
    if not ctx.store.has_rows(PRICE_EOD_DATASET, market, run_date):
        return _skip(run_date, "today's closing prices have not landed yet")
    watchlist = ctx.store.read_partition(STRATEGY_WATCHLIST_DATASET, market, run_date)
    eod = ctx.store.read_partition(PRICE_EOD_DATASET, market, run_date)
    hits = evaluate_close(watchlist, eod)
    _store_slot(ctx, run_date, CLOSE_SLOT, hits)
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.PRICE_EOD,
        market=ctx.market,
        as_of=f"{run_date.isoformat()}T{CLOSE_SLOT}",
        scope="strategy_watch",
    )
    met = sum(1 for h in hits if h.all_met)
    return journal_run(
        STRATEGY_HITS_DATASET,
        run_date,
        rows_written=len(hits),
        status=RunStatus.SUCCESS,
        detail=f"close: {len(hits)} breakout setups checked, {met} confirmed",
    )


async def job_strategy_intraday(run_date: date_, ctx: IngestContext) -> JobRun:
    return await run_strategy_slot(run_date, ctx, at=datetime.now(_TAIPEI).time())


__all__ = [
    "STRATEGY_HITS_DATASET",
    "job_strategy_close_check",
    "job_strategy_intraday",
    "run_strategy_slot",
]
