"""Startup catch-up for the 08:00 `strategy_watchlist` build.

The daemon never replays an elapsed window (`IngestDaemon.seed_startup`), so a daemon that was down
at 08:00 left the Watch page empty for the whole session even though the data to build the list was
there. `WatchlistCatchUp` builds today's list once the daemon is back, by calling the SAME job
function the schedule uses, so the job's own leader, calendar and stale-price gates apply unchanged.

Rules:
- Only on a TW trading day, from the job's fire time up to the session close. After the close or on
  a non-trading day nothing runs (logged once, at info).
- Never replaces a list: a day that already has a `strategy_watchlist` partition is left alone, so
  running this twice (or after the scheduled run) is a no-op.
- A run that does not produce the list (stale prices, error) is remembered in memory: the next try
  is an hour later, or on the first pass after a newer price partition has landed.
- Not a production hard-stop: the job only writes the system-wide setup list (no user data, no
  network call, no fetch burst), and it is a single idempotent daily job like
  `ingest/derived_catchup.py`, which the daemon also runs in every environment.
"""

from __future__ import annotations

from calendar.market_hours import MarketHours
from collections.abc import Awaitable, Callable
from datetime import date as date_
from datetime import datetime, timedelta
from datetime import time as time_

import structlog

from analytics.strategy_watch.batch import PRICE_EOD_DATASET, STRATEGY_WATCHLIST_DATASET
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run

logger = structlog.get_logger(__name__)

RETRY_AFTER = timedelta(hours=1)
CATCHUP_DETAIL = "startup catch-up"


class WatchlistCatchUp:
    """In-memory state of the day's catch-up; one instance per daemon."""

    def __init__(self) -> None:
        self._done_day: date_ | None = None
        self._noted_day: date_ | None = None
        self._retry_at: datetime | None = None
        self._price_day: date_ | None = None

    def _note(self, today: date_, reason: str) -> None:
        if self._noted_day != today:
            self._noted_day = today
            logger.info("ingest_watchlist_catchup_not_needed", reason=reason)

    @staticmethod
    def _latest_price_day(ctx: IngestContext) -> date_ | None:
        days = ctx.store.list_partition_dates(PRICE_EOD_DATASET, ctx.market.value)
        return days[-1] if days else None

    def _defer(self, ctx: IngestContext, local: datetime) -> None:
        self._retry_at = local + RETRY_AFTER
        self._price_day = self._latest_price_day(ctx)

    def _waiting(self, ctx: IngestContext, local: datetime) -> bool:
        """Whether a failed try is still inside its hour with no new prices to build from."""
        if self._retry_at is None or local >= self._retry_at:
            return False
        return self._latest_price_day(ctx) == self._price_day

    def _due(
        self, ctx: IngestContext, fire_time: time_, local: datetime, *, scheduled_ran: bool
    ) -> bool:
        """Whether a build should be tried now (every gate except the job's own)."""
        today = local.date()
        if self._done_day == today or local.time() < fire_time:
            return False  # before the fire time the scheduled run is still to come
        if not ctx.calendar.is_trading_day_extrapolating(today):
            self._note(today, "non-trading day")
            return False
        if local.time() > MarketHours().session_close:
            self._note(today, "after the session close")
            return False
        if ctx.store.has_partition(STRATEGY_WATCHLIST_DATASET, ctx.market.value, today):
            self._done_day = today
            return False
        if scheduled_ran:
            self._defer(ctx, local)  # it just ran and published nothing: do not run it twice
            return False
        return not self._waiting(ctx, local)

    async def maybe_run(
        self,
        ctx: IngestContext,
        fire_time: time_,
        run: Callable[[date_, IngestContext], Awaitable[JobRun]],
        local: datetime,
        *,
        scheduled_ran: bool,
    ) -> None:
        """Build today's list when it is missing and the session is not over. `scheduled_ran` is
        true when the scheduled job already had its turn in this very pass."""
        if not self._due(ctx, fire_time, local, scheduled_ran=scheduled_ran):
            return
        today = local.date()
        try:
            result = await run(today, ctx)
        except Exception:
            logger.exception("ingest_watchlist_catchup_failed", run_date=today.isoformat())
            self._defer(ctx, local)
            return
        journal_run(
            STRATEGY_WATCHLIST_DATASET,
            today,
            rows_written=result.rows_written,
            status=result.status,
            detail=f"{CATCHUP_DETAIL}: {result.detail}",
        )
        if result.status is RunStatus.SUCCESS:
            self._done_day = today
        else:
            self._defer(ctx, local)


__all__ = ["CATCHUP_DETAIL", "RETRY_AFTER", "WatchlistCatchUp"]
