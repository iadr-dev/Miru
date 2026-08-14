"""The platform-ingest daemon's scheduling core — turns the whole-market job functions into a
wall-clock-driven, leader-gated schedule that a thin process wrapper (`ingest.__main__`) loops.

Most jobs fire once per day at their own documented Taipei-local time, on trading days only; one
reference-data job (the industry-chain theme seed) fires on a monthly cadence instead, since its
source roster changes on a monthly-or-slower cadence. The daemon owns only the *when*: it decides
which jobs became due since the last pass and calls the existing job functions unchanged (their
bodies already enforce calendar-gating, leadership, and the batch-completion invalidation signal).
Responsibilities kept here, mirroring how the user-scheduled worker splits its single-pass logic
from its process loop:

1. **Leader-only.** `LeaderElection.is_leader()` is checked once per pass before any job is
   considered — a non-leader pass is a silent no-op so a standby replica loops harmlessly.
2. **Wall-clock cadence.** A daily job is due when its Taipei-local fire time has passed for the
   current local day and it has not already fired today; a monthly job is due when its fire time
   has passed and it has never fired or last fired at least a rolling ~4-week window ago. Marking
   a job fired *before* running it means a failed run is never retried in a tight loop — the next
   window's pass (or an explicit gap-fill) retries, matching the job functions' own no-retry-loop
   contract.
3. **Boot clean.** On startup, any daily job whose fire time already passed today is seeded as
   already-handled, so a restart never replays a window that already elapsed. Catching up on a
   genuinely missed window is the dev-only startup backfill's job, not the daemon's — production
   boots without a startup fetch burst.
4. **Trading-day gate.** A daily job that would fire on a non-trading day is skipped (and marked
   handled for the day) using the shared trading-day calendar, so a holiday/weekend is a graceful
   no-op. A monthly reference-data job is not trading-day-gated — its roster is published
   independent of trading sessions.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as date_
from datetime import time as time_
from enum import StrEnum
from zoneinfo import ZoneInfo

import structlog

from analytics.screener.batch import job_screener_flags
from analytics.scoring_batch import job_scoring_batch
from analytics.themes.seed import seed_industry_themes
from analytics.themes.tiers_seed import seed_theme_tiers
from config.settings import Settings, get_settings
from ingest.context import IngestContext
from ingest.jobs import (
    job_active_etf_holding_changes,
    job_active_etf_holdings,
    job_balance_sheet,
    job_cash_flow,
    job_dividends,
    job_financials,
    job_institutional_flow,
    job_market_value,
    job_price_eod,
)
from db.base import ensure_schema_for, make_engine, make_session_factory
from ingest.journal import JobRun
from ingest.leader import get_default_leader_election
from ingest.store import ParquetStore
from ingest.us_jobs import job_us_fundamentals, job_us_price_eod
from ingest.us_universe import job_us_symbol_universe
from sources.finmind import FinMindAdapter
from sources.yfinance import YFinanceAdapter

logger = structlog.get_logger(__name__)

_TAIPEI = ZoneInfo("Asia/Taipei")

# A monthly job re-fires once its last handled date is at least this many days in the past — a
# rolling ~4-week gap rather than a fixed calendar day-of-month, so it stays robust across
# month-length differences and a missed trigger day without a per-month calendar bookkeeping.
_MONTHLY_MIN_GAP_DAYS = 28

# Same rolling-gap reasoning as `_MONTHLY_MIN_GAP_DAYS`, one week instead of ~4.
_WEEKLY_MIN_GAP_DAYS = 7

# The one async signature every whole-market job function already presents.
IngestJobFn = Callable[[date_, IngestContext], Awaitable[JobRun]]


class Cadence(StrEnum):
    """How often a scheduled job may fire. `DAILY` fires at most once per local trading day and
    is trading-day-gated; `WEEKLY` fires at most once per rolling ~7-day window
    (`_WEEKLY_MIN_GAP_DAYS`) and, like `MONTHLY`, is NOT trading-day-gated — both are reference-
    data cadences (a US financials/dividends filing is not tied to a trading session, same
    reasoning as the industry-chain theme seed); `MONTHLY` fires at most once per rolling ~4-week
    window (`_MONTHLY_MIN_GAP_DAYS`) — the right granularity for reference data that changes on
    a monthly-or-slower cadence, so it is not re-pulled every day."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True)
class ScheduledIngestJob:
    """One job's fixed fire time (Taipei local wall clock) and cadence bound to its job function.

    The fire time is a code constant, not config: it tracks the upstream's own publish clock (a
    ~10-minute buffer after the dataset is published), which is a property of the data source, not
    a per-deployment tunable. Only the poll cadence that decides how promptly the loop notices a
    due job is a setting.
    """

    name: str
    fire_time: time_
    run: IngestJobFn
    cadence: Cadence = Cadence.DAILY


# The six whole-market pulls, each at the Taipei-local time its own job docstring documents.
SCHEDULED_JOBS: tuple[ScheduledIngestJob, ...] = (
    ScheduledIngestJob("price_eod", time_(17, 40), job_price_eod),
    ScheduledIngestJob("institutional_flow", time_(20, 10), job_institutional_flow),
    # Screener-flag batch — a derived-compute pass, not a FinMind pull: it reads the price and
    # institutional-flow partitions the two jobs above just wrote and evaluates the tag catalog
    # over them. Fires at 20:30, after the 20:10 institutional-flow pull, so both its inputs have
    # landed for the day; placed here (rather than folded in later) so a reader sees it sequence
    # right after its dependencies in tuple order too. Daily and trading-day-gated like the pulls.
    ScheduledIngestJob("screener_flags", time_(20, 30), job_screener_flags),
    ScheduledIngestJob("active_etf_holdings", time_(21, 15), job_active_etf_holdings),
    ScheduledIngestJob(
        "active_etf_holding_changes", time_(21, 15), job_active_etf_holding_changes
    ),
    ScheduledIngestJob("financials", time_(21, 30), job_financials),
    ScheduledIngestJob("dividends", time_(21, 30), job_dividends),
)


class IngestDaemon:
    """The leader-gated, wall-clock scheduling core. `run_due_jobs()` is one pass — directly
    callable with an injected clock so the schedule is testable without a real loop or a sleep."""

    def __init__(
        self,
        ctx: IngestContext,
        *,
        jobs: Sequence[ScheduledIngestJob] = SCHEDULED_JOBS,
        tz: ZoneInfo = _TAIPEI,
    ) -> None:
        self._ctx = ctx
        self._jobs = tuple(jobs)
        self._tz = tz
        # The local day on which each job last fired (or was skipped), so a job fires at most once
        # per local day regardless of how often the loop polls.
        self._last_handled: dict[str, date_] = {}
        self._seeded = False

    def _local(self, now: datetime) -> datetime:
        return now.astimezone(self._tz) if now.tzinfo is not None else now.replace(tzinfo=self._tz)

    def seed_startup(self, *, now: datetime | None = None) -> None:
        """Mark every daily job whose fire time already elapsed today as handled, so the first pass
        after a restart does not replay an already-past window (production boots clean). Monthly
        jobs are deliberately not seeded: a restart should still let a due monthly window fire, and
        its own rolling-gap gate already prevents a tight replay."""
        local = self._local(now or datetime.now(UTC))
        for job in self._jobs:
            if job.cadence is Cadence.DAILY and local.time() >= job.fire_time:
                self._last_handled[job.name] = local.date()
        self._seeded = True

    def _is_due(self, job: ScheduledIngestJob, today: date_) -> bool:
        """Whether `job` should fire this pass, given the day it last fired. A daily job is due
        once per local day; a weekly/monthly job is due when it has never fired or last fired at
        least its own rolling-gap window ago (`_WEEKLY_MIN_GAP_DAYS`/`_MONTHLY_MIN_GAP_DAYS`)."""
        last = self._last_handled.get(job.name)
        if last is None:
            return True
        if job.cadence is Cadence.MONTHLY:
            return (today - last).days >= _MONTHLY_MIN_GAP_DAYS
        if job.cadence is Cadence.WEEKLY:
            return (today - last).days >= _WEEKLY_MIN_GAP_DAYS
        return last != today

    async def run_due_jobs(self, *, now: datetime | None = None) -> list[str]:
        """Fire every job that has become due since the last pass. Returns the names of jobs that
        actually ran (a non-trading-day skip does not count as run). A non-leader pass returns an
        empty list without touching any job — the standby contract."""
        if not self._ctx.leader.is_leader():
            logger.info("ingest_skip_not_leader")
            return []
        local = self._local(now or datetime.now(UTC))
        today = local.date()
        ran: list[str] = []
        for job in self._jobs:
            if local.time() < job.fire_time:
                continue
            if not self._is_due(job, today):
                continue
            # Mark handled before running: a failure must not tight-loop within the window. The
            # next window's fire (or an explicit gap-fill) retries, matching the job's no-retry
            # contract.
            self._last_handled[job.name] = today
            # Monthly reference-data jobs are not tied to the market calendar (the classification
            # roster is published independent of trading sessions), so only daily market pulls are
            # trading-day-gated.
            if job.cadence is Cadence.DAILY and not self._ctx.calendar.is_trading_day(today):
                logger.info(
                    "ingest_job_skipped_non_trading_day",
                    job=job.name,
                    run_date=today.isoformat(),
                )
                continue
            try:
                result = await job.run(today, self._ctx)
            except Exception:
                # Isolate a job's failure so one bad pull never blocks the others in this pass;
                # already marked handled, so it retries next day rather than looping now.
                logger.exception("ingest_job_errored", job=job.name, run_date=today.isoformat())
                continue
            logger.info(
                "ingest_job_fired",
                job=job.name,
                run_date=today.isoformat(),
                status=result.status.value,
                rows_written=result.rows_written,
            )
            ran.append(job.name)
        return ran


async def build_ingest_context_from_settings(settings: Settings | None = None) -> IngestContext:
    """Assemble the job context from config — the FinMind adapter, the leader-only Parquet writer,
    the shared leader-election seam, and the trading-day calendar. No network I/O at construction.

    The batch-completion invalidation publisher is left at its no-op default: the concrete
    cross-process transport lives in an upper layer the ingest layer may not import under the
    one-way dependency rule, so an ingest-owned composition can only bind the seam's no-op. The
    partition still lands; clients fall back to boundary-anchored revalidation.

    `async def` (not sync) so it can `await ensure_schema_for(...)` on the caller's own already-
    running startup loop — this project's sole schema-provisioning mechanism for the relational
    session factory below (the daily Parquet jobs never touch it, but the theme-seed job does).
    """
    resolved = settings or get_settings()
    session_factory = make_session_factory(make_engine(resolved.database_url))
    await ensure_schema_for(session_factory)
    return IngestContext(
        finmind=FinMindAdapter(resolved),
        store=ParquetStore(root=resolved.parquet_root),
        leader=get_default_leader_election(),
        calendar=TradingDayCalendar(),
        tls_policy=resolved.tls_policy,
        # The relational session factory the theme-seed job writes through — the daily Parquet
        # jobs never touch it. Built here from the same database URL the rest of the app uses.
        session_factory=session_factory,
        # Only the US whole-market jobs (`ingest/us_jobs.py`) use this.
        yfinance=YFinanceAdapter(),
    )


async def build_ingest_daemon_from_settings(settings: Settings | None = None) -> IngestDaemon:
    """The composition root the ingest process invokes."""
    return IngestDaemon(await build_ingest_context_from_settings(settings))


__all__ = [
    "SCHEDULED_JOBS",
    "Cadence",
    "IngestDaemon",
    "IngestJobFn",
    "ScheduledIngestJob",
    "build_ingest_context_from_settings",
    "build_ingest_daemon_from_settings",
]
