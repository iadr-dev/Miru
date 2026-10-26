"""Gap-fill + bounded backfill — purpose-bounded to building/maintaining the platform's own
historical store, nothing else.

Both gap-fill (fill a handful of missed trading days) and backfill (a longer historical
window) reuse the exact same per-date code path as the daily cron
(`ingest.jobs.run_whole_market_job`): same code path as first-run, just scoped to the gap.
The only difference between "gap-fill" and "backfill" here is the *size* of the date range
requested; there is deliberately no second write path.
"""

from __future__ import annotations

from datetime import date as date_

import structlog

from ingest.context import IngestContext
from ingest.jobs import WholeMarketJobSpec, run_whole_market_job
from ingest.journal import JobRun

logger = structlog.get_logger(__name__)


def detect_missing_trading_days(
    spec: WholeMarketJobSpec,
    ctx: IngestContext,
    *,
    start: date_,
    end: date_,
) -> tuple[date_, ...]:
    """Diff the expected trading-day list against the dates actually present in the store for
    `spec.dataset`: any gap between expected trading days and what's actually partitioned
    becomes a targeted date-range backfill."""
    expected = (end,) if spec.snapshot_only else ctx.calendar.business_days_between(start, end)
    # `has_rows`, not `has_partition`: a day an earlier run recorded as an empty placeholder
    # (fetched before the upstream had published it) is still a gap.
    # A day an earlier run confirmed as a market closure (journaled SKIPPED) is done, not a gap; a
    # FAILED run leaves no marker, so it is retried.
    market = ctx.market.value
    return tuple(
        d
        for d in expected
        if not ctx.store.has_rows(spec.dataset, market, d)
        and not ctx.store.is_closed(spec.dataset, market, d)
    )


async def backfill_range(
    spec: WholeMarketJobSpec,
    ctx: IngestContext,
    *,
    start: date_,
    end: date_,
    only_missing: bool = True,
) -> tuple[JobRun, ...]:
    """Run `spec` for every trading day in `[start, end]` (or only the missing ones, the
    gap-fill mode — `only_missing=True`, the default). Bounded, checkpoint-free per-call
    resumability comes from idempotency: re-running this for a date already written is a
    no-op-shaped overwrite, so a partial run can simply be re-invoked — the checkpoint *is*
    "which dates already have a partition", so it's checkpointed and resumable without any
    separate checkpoint state.

    Cadence-bounded: this is an on-demand/dev-triggered call, not a cron itself — the caller
    (preflight, or a future backfill operator command) decides when to invoke it. It still
    calls through `run_whole_market_job`, so every date is individually calendar-gated and
    leader-gated exactly like the daily cron job.
    """
    dates = (
        detect_missing_trading_days(spec, ctx, start=start, end=end)
        if only_missing
        else ctx.calendar.business_days_between(start, end)
    )
    runs: list[JobRun] = []
    for run_date in dates:
        runs.append(await run_whole_market_job(spec, run_date, ctx))
    logger.info(
        "ingest_backfill_completed",
        dataset=spec.dataset,
        start=start.isoformat(),
        end=end.isoformat(),
        dates_processed=len(runs),
        only_missing=only_missing,
    )
    return tuple(runs)


__all__ = ["backfill_range", "detect_missing_trading_days"]
