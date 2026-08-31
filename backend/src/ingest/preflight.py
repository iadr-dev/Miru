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
from zoneinfo import ZoneInfo

import structlog

from ingest.backfill import backfill_range
from ingest.context import IngestContext
from ingest.jobs import WholeMarketJobSpec
from ingest.journal import JobRun

logger = structlog.get_logger(__name__)

_TAIPEI = ZoneInfo("Asia/Taipei")

# TaiwanStockPrice's documented FinMind `Update:` time (Taipei-local) — before this, "today" is
# not yet published, so the preflight targets yesterday instead.
_FINMIND_EOD_PUBLISH_TIME = time_(17, 30)
_ONE_DAY = timedelta(days=1)


def last_available_trading_day(calendar: TradingDayCalendar, *, now: datetime) -> date_:
    """The most recent trading day whose EOD data FinMind has actually published, respecting
    the FinMind publish lag. `now` may be ANY timezone-aware instant (every real caller passes
    `datetime.now(UTC)`) — this function converts to Asia/Taipei wall-clock time itself before
    comparing against `_FINMIND_EOD_PUBLISH_TIME`, since that publish time is a Taipei-local
    clock reading, not a UTC one. A naive (tzinfo-less) `now` is assumed to already be Taipei
    local time, unchanged.

    Confirmed real bug this conversion fixes: every prior caller passed raw UTC straight into a
    `now.time() >= 17:30` (Taipei) comparison with no conversion — correct only by coincidence
    for whatever UTC hour happened to be called, wrong the other roughly half of the day (Taipei
    is UTC+8, so e.g. UTC 10:00 is Taipei 18:00 — already past the Taipei publish time — but the
    unconverted comparison would have read UTC 10:00 as "before 17:30" and wrongly targeted
    yesterday instead of today).

    Uses the extrapolating day-check, not the strict allow-list one: `target` is often "today",
    which is always beyond `calendar`'s known range until today's own EOD ingest has landed — a
    strict check here would wrongly treat a legitimate, not-yet-ingested today as a non-trading
    day and walk forward into the future instead of returning it. See
    `TradingDayCalendar.is_trading_day_extrapolating`'s own docstring."""
    local = now.astimezone(_TAIPEI) if now.tzinfo is not None else now
    today = local.date()
    target = today if local.time() >= _FINMIND_EOD_PUBLISH_TIME else today - _ONE_DAY
    if calendar.is_trading_day_extrapolating(target):
        return target
    # "Most recent AVAILABLE trading day" must walk backward, never forward — `target` itself
    # is not a trading day here (e.g. a weekend/holiday), so the answer is a real day already in
    # the past, not a future date that hasn't traded yet.
    return calendar.previous_trading_day(target, inclusive=False)


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
