"""Dev-only startup catch-up (`INGEST_ON_STARTUP`) — a dev-only startup catch-up is permitted
as a *manual* trigger of the same gap-fill logic the daily cron uses; production boots clean
with no startup network calls.

There is no app entrypoint yet (that's a later phase) — `run_preflight_if_enabled()` is
built ready to be called from that entrypoint's startup hook once it exists; it is not wired
to anything today and performs no import-time side effects (constructing it does nothing
until a caller `await`s it).

**Not authorized in production.** `Settings.ingest_on_startup` MUST be `false` in prod
(`.env.example`); this function itself also refuses to run unless the flag is set, so even an
accidental call from a future entrypoint is a no-op in prod config (defense in depth, not just
"someone remembered to gate the call site").
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import UTC, datetime, timedelta
from datetime import date as date_
from datetime import time as time_

import structlog

from ingest.backfill import backfill_range
from ingest.context import IngestContext
from ingest.jobs import WholeMarketJobSpec
from ingest.journal import JobRun

logger = structlog.get_logger(__name__)

# TaiwanStockPrice's documented FinMind `Update:` time — before this, "today" is not yet
# published, so the preflight targets yesterday instead.
_FINMIND_EOD_PUBLISH_TIME = time_(17, 30)
_ONE_DAY = timedelta(days=1)


def last_available_trading_day(calendar: TradingDayCalendar, *, now: datetime) -> date_:
    """The most recent trading day whose EOD data FinMind has actually published, respecting
    the FinMind publish lag. `now` must be Asia/Taipei wall-clock time (or already
    UTC+8-adjusted by the caller) — this function does no timezone conversion."""
    today = now.date()
    target = today if now.time() >= _FINMIND_EOD_PUBLISH_TIME else today - _ONE_DAY
    if calendar.is_trading_day(target):
        return target
    return calendar.next_trading_day(target, inclusive=False)


async def run_preflight_if_enabled(
    spec: WholeMarketJobSpec,
    ctx: IngestContext,
    *,
    ingest_on_startup: bool,
    dev_backfill_years: int,
    now: datetime | None = None,
) -> tuple[JobRun, ...]:
    """The `INGEST_ON_STARTUP` gap-fill entrypoint.

    - Gated behind `ingest_on_startup` — returns `()` immediately (no fetch, no leader check)
      when `False`, which is the production default (`.env.example`: "MUST be false in
      production").
    - Sweeps `ctx.store`'s orphaned temp dirs first — the recovery for a crashed mid-COPY
      write on a prior leader startup — cheap, purely local filesystem, no network — before
      touching FinMind.
    - Requests the bounded dev window `[target - dev_backfill_years, target]` via
      `backfill_range(only_missing=True)`, which already skips any date already partitioned
      (the same gap-fill diff the daily cron path uses) — so an empty store naturally gets a
      first-run backfill and a non-empty store naturally gets just its actual gap, through one
      call.
    """
    if not ingest_on_startup:
        return ()

    ctx.store.sweep_orphaned_temp_dirs()
    moment = now or datetime.now(UTC)
    target = last_available_trading_day(ctx.calendar, now=moment)
    start = ctx.calendar.shift_business_days(target, -(dev_backfill_years * 250))

    logger.info(
        "ingest_preflight_started",
        dataset=spec.dataset,
        start=start.isoformat(),
        target=target.isoformat(),
        dev_backfill_years=dev_backfill_years,
    )
    return await backfill_range(spec, ctx, start=start, end=target, only_missing=True)


__all__ = ["last_available_trading_day", "run_preflight_if_enabled"]
