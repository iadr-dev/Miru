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

from calendar.trading_days import TradingDayCalendar, calendar_from_known_dates
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from datetime import date as date_
from datetime import time as time_
from enum import StrEnum
from zoneinfo import ZoneInfo

import structlog

from analytics.scoring_batch import job_scoring_batch, job_trend_score_batch
from analytics.screener.batch import job_screener_flags
from analytics.strategy_watch.batch import job_strategy_watchlist
from analytics.themes.seed import seed_industry_themes
from analytics.themes.tiers_seed import seed_theme_tiers
from config.settings import Settings, get_settings
from db.base import ensure_schema_for, make_engine, make_session_factory
from ingest.context import IngestContext
from ingest.jobs import (
    PRICE_EOD_JOB,
    job_active_etf_holding_changes,
    job_active_etf_holdings,
    job_attention_stocks,
    job_balance_sheet,
    job_cash_flow,
    job_disposition_events,
    job_dividends,
    job_etf_aum,
    job_etf_expense_ratio,
    job_etf_nav,
    job_financials,
    job_insider_transfer_declaration,
    job_institutional_flow,
    job_margin_short,
    job_market_value,
    job_market_value_weight,
    job_material_announcements,
    job_month_revenue,
    job_price_eod,
    job_shareholding_distribution,
    job_stock_info,
    job_valuation_ratios,
)
from ingest.jobs.strategy_intraday import job_strategy_intraday
from ingest.journal import JobRun
from ingest.leader import build_leader_election, refresh_leadership, release_leadership
from ingest.preflight import last_completed_us_session
from ingest.publisher_binding import resolve_data_update_publisher
from ingest.store import ParquetStore
from ingest.us_jobs import US_PRICE_EOD_DATASET, job_us_fundamentals, job_us_price_eod
from ingest.us_universe import job_us_symbol_universe
from schemas.enums import Market
from sources.finmind import FinMindAdapter, FinMindQuotaRefusedError, build_finmind_quota
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
    a monthly-or-slower cadence, so it is not re-pulled every day. `INTRADAY` fires once per
    `every_minutes` slot from `fire_time` through `until` on a trading day (the 策略盯盤 check
    during the TW session); it is the only cadence that fires more than once a day."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    INTRADAY = "intraday"


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
    # Which calendar the daemon's own daily trading-day gate (below) checks THIS job against —
    # not merely which market the job internally scopes its own data reads to. `us_price_eod`/
    # `us_fundamentals`/`_us_scoring_batch`/`_us_trend_score_batch` must gate on `Market.US`
    # (US market holidays), never the TW calendar `run_due_jobs` otherwise defaults every job
    # to — a TW-only closure (Lunar New Year, Double Ten, etc.) is an ordinary US trading day and
    # must not silently skip a US job (this was a real latent bug: every existing US daily job
    # was gated against the TW calendar before this field existed, since `run_due_jobs` checked
    # `self._ctx.calendar` unconditionally for every `Cadence.DAILY` job regardless of which
    # market it actually served).
    market: Market = Market.TW
    # `Cadence.INTRADAY` only: the last slot start (inclusive) and the slot length.
    until: time_ | None = None
    every_minutes: int = 0


async def _us_scoring_batch(run_date: date_, ctx: IngestContext) -> JobRun:
    """`job_scoring_batch`, but against a `Market.US`-scoped view of the SAME shared `ctx`
    (`store`/`leader`/`session_factory`/`yfinance` all reused as-is — only `market` differs).
    `IngestDaemon` holds exactly one `IngestContext` (`self._ctx`, TW by default); this thin
    wrapper is how a US-scoped batch pass gets registered in `SCHEDULED_JOBS` without standing up
    a second daemon instance or a second full context construction. `dataclasses.replace` is
    cheap here since `IngestContext` is frozen and every field but `market` is a shared reference
    (the same `ParquetStore`/`LeaderElection`/session factory the TW jobs already use), not a
    fresh copy. Graceful degradation for the momentum/trend_quality-only US case (no
    institutional-flow/market-value/ETF datasets ingested for US) already lives inside
    `job_scoring_batch` itself — see `analytics/scoring_batch/`'s own market-branching
    comments — so no US-specific logic belongs in this wrapper."""
    return await job_scoring_batch(run_date, replace(ctx, market=Market.US))


async def _us_trend_score_batch(run_date: date_, ctx: IngestContext) -> JobRun:
    """`job_trend_score_batch`'s US counterpart — see `_us_scoring_batch`'s own docstring for why
    this thin `dataclasses.replace`-based wrapper exists rather than a second daemon instance."""
    return await job_trend_score_batch(run_date, replace(ctx, market=Market.US))


# The whole-market pulls, each at the Taipei-local time its own job docstring documents.
SCHEDULED_JOBS: tuple[ScheduledIngestJob, ...] = (
    # Stock/ETF universe reference roster (industry classification) — fires first, well before
    # the trading day's own post-close pulls, so `get_sector_heatmap`'s industry join against the
    # same day's `price_eod` partition always has a fresh roster available (STOCK_INFO_JOB's own
    # comment: once-daily, off-peak — no other REFERENCE-tier FinMind dataset is on a daily cron
    # today, only monthly ones, but this one is deliberately daily so same-day
    # listings/reclassifications aren't stale for up to a month).
    ScheduledIngestJob("stock_info", time_(6, 0), job_stock_info),
    # 策略盯盤 pre-market screen — derived compute over the last session's landed price/flow/margin
    # partitions and the event feeds; writes today's watchlist with each stock's trigger levels
    # before the 09:00 open, so the intraday check and the morning-list alert have it to read.
    ScheduledIngestJob("strategy_watchlist", time_(8, 0), job_strategy_watchlist),
    # 策略盯盤 intraday check — one whole-market Sponsor snapshot per 10-minute slot through the
    # session (09:00 open to the 13:30 close), compared against the morning watchlist.
    ScheduledIngestJob(
        "strategy_intraday",
        time_(9, 0),
        job_strategy_intraday,
        Cadence.INTRADAY,
        until=time_(13, 30),
        every_minutes=10,
    ),
    ScheduledIngestJob("price_eod", time_(17, 40), job_price_eod),
    # Margin-purchase/short-sale balance snapshot — shares `price_eod`'s ~17:30 FinMind publish
    # clock (MARGIN_SHORT_JOB's own cron_hint); feeds the 融資增減 TW-rankings tab.
    ScheduledIngestJob("margin_short", time_(17, 40), job_margin_short),
    # PER/PBR/dividend-yield snapshot — ~18:00 post-close publish clock (VALUATION_RATIOS_JOB's
    # own cron_hint); feeds the 股息殖利率 TW-rankings tab.
    ScheduledIngestJob("valuation_ratios", time_(18, 10), job_valuation_ratios),
    ScheduledIngestJob("institutional_flow", time_(20, 10), job_institutional_flow),
    # Screener-flag batch — a derived-compute pass, not a FinMind pull: it reads the price and
    # institutional-flow partitions the two jobs above just wrote and evaluates the tag catalog
    # over them. Fires at 20:30, after the 20:10 institutional-flow pull, so both its inputs have
    # landed for the day; placed here (rather than folded in later) so a reader sees it sequence
    # right after its dependencies in tuple order too. Daily and trading-day-gated like the pulls.
    ScheduledIngestJob("screener_flags", time_(20, 30), job_screener_flags),
    ScheduledIngestJob("active_etf_holdings", time_(21, 15), job_active_etf_holdings),
    ScheduledIngestJob("active_etf_holding_changes", time_(21, 15), job_active_etf_holding_changes),
    # ETF premium/discount-to-NAV snapshot (TWSE MIS `all_etf.txt`, not FinMind) — feeds the
    # ETF-specific score breakdown's premium/discount factor; must land before `scoring_batch`
    # (23:55) below, so it fires in the same 21:xx window as the other ETF-related pulls.
    ScheduledIngestJob("etf_nav", time_(21, 20), job_etf_nav),
    # ETF/fund internal-fee (內扣費用) whole-market snapshot (SITCA IN2105, not FinMind) — feeds
    # the ETF-specific score breakdown's `cost` factor. Monthly cadence: SITCA's own report only
    # refreshes monthly (same reasoning as the `theme_industry_seed`/`theme_tier_seed` monthly
    # jobs above); not trading-day-gated for the same reason those aren't. Fires in the same
    # 21:xx window as `etf_nav` so both ETF-specific pulls land before `scoring_batch` (23:55).
    ScheduledIngestJob("etf_expense_ratio", time_(21, 25), job_etf_expense_ratio, Cadence.MONTHLY),
    # ETF total-net-assets (資產規模) — Yahoo's own `totalAssets` per symbol, paced fan-out over
    # the whole `etf_nav` roster (yfinance has no bulk ETF form; see jobs.py's own docstring for
    # why this is the accepted per-symbol exception). AUM changes far slower than daily price, so
    # weekly is enough — same reasoning `us_fundamentals` uses for its own weekly cadence. Fires
    # after `etf_nav` (21:20) since it reads that job's roster, before `scoring_batch` (23:55).
    ScheduledIngestJob("etf_aum", time_(21, 27), job_etf_aum, Cadence.WEEKLY),
    ScheduledIngestJob("financials", time_(21, 30), job_financials),
    ScheduledIngestJob("dividends", time_(21, 30), job_dividends),
    # Balance-sheet/cash-flow share FINANCIALS_JOB's own irregular-per-filing cadence and fire
    # alongside it (jobs.py's own comment: "ingested alongside the income statement" so the
    # derived value factor's ROE/debt-equity/ROIC/EV-EBITDA/FCF-yield sub-metrics have every
    # statement family available together).
    ScheduledIngestJob("balance_sheet", time_(21, 30), job_balance_sheet),
    ScheduledIngestJob("cash_flow", time_(21, 30), job_cash_flow),
    # Monthly-revenue filings — same irregular per-filing cadence as the statement family above;
    # feeds `revenue_yoy`'s `analytics/fundamentals.py` compute in `get_company_profile`.
    ScheduledIngestJob("month_revenue", time_(21, 30), job_month_revenue),
    # MOPS whole-market disclosure feeds — not FinMind-sourced (jobs.py's own `job_etf_nav`-style
    # carve-out); no fixed publish clock (disclosures land throughout the trading day and
    # after-hours), so this slot is a plain daily snapshot time, not a "just after publish"
    # buffer the way most other slots above are. See `job_material_announcements`'s own
    # docstring for why these two ALSO run from `ingest/__main__.py`'s startup gap-fill path.
    ScheduledIngestJob("material_announcements", time_(21, 35), job_material_announcements),
    ScheduledIngestJob(
        "insider_transfer_declaration", time_(21, 35), job_insider_transfer_declaration
    ),
    # TWSE+TPEX 注意股/處置 whole-market announcement feeds — the 處置王 board's data source. Not
    # FinMind-sourced (same non-FinMind carve-out as the MOPS pair above); no fixed publish clock
    # either, so this slot is likewise a plain daily snapshot time. See `job_attention_stocks`'s
    # own docstring for why these two ALSO run from `ingest/__main__.py`'s startup gap-fill path.
    ScheduledIngestJob("attention_stocks", time_(21, 35), job_attention_stocks),
    ScheduledIngestJob("disposition_events", time_(21, 35), job_disposition_events),
    # Market cap — Mon-Fri ~23:30 FinMind publish clock (MARKET_VALUE_JOB's own cron_hint).
    ScheduledIngestJob("market_value", time_(23, 40), job_market_value),
    # Market-cap index weight — the paired FinMind publish alongside market_value above (same
    # ~23:30 publish clock, MARKET_VALUE_WEIGHT_JOB's own cron_hint); fires at the same slot.
    ScheduledIngestJob("market_value_weight", time_(23, 40), job_market_value_weight),
    # Multi-factor composite score — a derived-compute pass over the day's already-landed price/
    # flow/fundamental/market-value partitions (`get_stock_score`/`get_score_leaderboard`'s cache-
    # only read backend). Fires last, after every one of its own inputs (price_eod,
    # institutional_flow, financials/balance_sheet/cash_flow, market_value) has landed for the
    # day — `job_scoring_batch`'s own docstring: idempotent, leader- and calendar-gated like every
    # other batch here.
    ScheduledIngestJob("scoring_batch", time_(23, 55), job_scoring_batch),
    # 趨勢評分 (trend score) — a second, independent derived-compute pass over the same landed
    # price/flow/margin-short partitions plus a live TAIEX read (`get_trend_score`'s cache-backed
    # five-dimension read; the sixth, broker_concentration, is computed on demand by that tool, not
    # this batch — see `job_trend_score_batch`'s own docstring). Fires right after `scoring_batch`
    # since both are derived-compute passes over the same day's already-ingested inputs.
    ScheduledIngestJob("trend_score_batch", time_(23, 57), job_trend_score_batch),
    # US symbol universe — monthly reference data (not trading-day-gated, mirrors the
    # industry-chain theme seed's own cadence reasoning); must land before the price/fundamentals
    # pulls below have anything to fetch, so it fires earliest in the Taipei morning.
    ScheduledIngestJob("us_symbol_universe", time_(5, 30), job_us_symbol_universe, Cadence.MONTHLY),
    # Industry-chain theme seed (Layer 1) — monthly reference data derived from FinMind's
    # whole-market industry-chain classification roster, feeding `theme-lookup`'s
    # `get_symbol_themes`/`get_theme_members`. Fires after the price_eod window so this pass
    # doesn't compete with it for FinMind quota; tier assignment (Layer 2, below) must fire AFTER
    # this has written the month's memberships, since it classifies sub-industries actually
    # present in them.
    ScheduledIngestJob("theme_industry_seed", time_(18, 30), seed_industry_themes, Cadence.MONTHLY),
    # Theme tier seed (Layer 2) — pure derived-compute over the memberships the job above just
    # wrote plus the curated `config/theme_tiers.yaml` map; no FinMind call of its own, so it
    # fires immediately after with no quota concern.
    ScheduledIngestJob("theme_tier_seed", time_(18, 35), seed_theme_tiers, Cadence.MONTHLY),
    # US price EOD — US markets close 16:00 ET (UTC-4/-5), i.e. ~04:00-05:00 Taipei the next
    # calendar day; 06:00 Taipei gives yfinance's own upstream data a comfortable landing buffer
    # past that close before this pulls. `job_us_price_eod` is calendar-gated the same Mon-Fri
    # weekday way `is_trading_day` degrades to for any market (see its own docstring) — a US
    # market holiday just lands as a legitimate empty response, same as a TW non-trading day.
    ScheduledIngestJob("us_price_eod", time_(6, 0), job_us_price_eod, market=Market.US),
    # US fundamentals — per-symbol yfinance fan-out (no bulk endpoint), so it runs later in the
    # Taipei morning, well clear of the price pull's own network usage. Weekly: fundamentals
    # change far slower than daily price EOD (see `job_us_fundamentals`'s own docstring).
    ScheduledIngestJob(
        "us_fundamentals", time_(7, 0), job_us_fundamentals, Cadence.WEEKLY, market=Market.US
    ),
    # US scoring/trend-score batches — the direct US counterpart to `scoring_batch`/
    # `trend_score_batch` above (23:55/23:57 Taipei, right after that day's TW pulls land), fired
    # here instead since `us_price_eod` publishes ~06:00 Taipei, not late TW-evening: 07:30 gives
    # `job_us_fundamentals` (07:00) a comfortable landing buffer first. Both wrappers
    # (`_us_scoring_batch`/`_us_trend_score_batch`) run against a `Market.US`-scoped view of the
    # SAME shared `ctx` via `dataclasses.replace` — see their own docstrings.
    ScheduledIngestJob("us_scoring_batch", time_(7, 30), _us_scoring_batch, market=Market.US),
    ScheduledIngestJob(
        "us_trend_score_batch", time_(7, 32), _us_trend_score_batch, market=Market.US
    ),
    # TDCC/FinMind 股權持股分級表 — weekly (published for each week's final trading day; the job
    # itself resolves the exact publish date, see `job_shareholding_distribution`'s own
    # docstring). Fires at the same 06:00 off-peak slot `stock_info` uses, well before the
    # trading day's own post-close pulls, since it has no same-day-relevance ordering
    # dependency on anything else in this list.
    ScheduledIngestJob(
        "shareholding_distribution", time_(6, 5), job_shareholding_distribution, Cadence.WEEKLY
    ),
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
        # The US session each daily US job last ran for — a daily US job fires for the last
        # *completed US session*, not the Taipei date, so a Sunday/Monday-morning pass (no new
        # session since Friday) must not re-pull Friday.
        self._last_session: dict[str, date_] = {}
        # The (day, slot index) each intraday job last fired for, so it fires once per slot.
        self._last_slot: dict[str, tuple[date_, int]] = {}
        self._seeded = False

    def _local(self, now: datetime) -> datetime:
        return now.astimezone(self._tz) if now.tzinfo is not None else now.replace(tzinfo=self._tz)

    @staticmethod
    def _is_us_daily(job: ScheduledIngestJob) -> bool:
        return job.market is Market.US and job.cadence is Cadence.DAILY

    def seed_startup(self, *, now: datetime | None = None) -> None:
        """Mark every daily job whose fire time already elapsed today as handled, so the first pass
        after a restart does not replay an already-past window (production boots clean). Monthly
        jobs are deliberately not seeded: a restart should still let a due monthly window fire, and
        its own rolling-gap gate already prevents a tight replay."""
        local = self._local(now or datetime.now(UTC))
        for job in self._jobs:
            if job.cadence is Cadence.DAILY and local.time() >= job.fire_time:
                self._last_handled[job.name] = local.date()
                if self._is_us_daily(job):
                    self._last_session[job.name] = last_completed_us_session(
                        self._ctx.us_calendar, now=local
                    )
        self._seeded = True

    def _intraday_slot_due(self, job: ScheduledIngestJob, local: datetime) -> bool:
        """Whether `local` is inside the job's window in a slot it has not fired for yet; marks
        the slot handled when it is (a failed slot is not retried, the next slot runs)."""
        if job.until is None or job.every_minutes <= 0 or local.time() > job.until:
            return False
        start = job.fire_time.hour * 60 + job.fire_time.minute
        slot = (local.hour * 60 + local.minute - start) // job.every_minutes
        key = (local.date(), slot)
        if self._last_slot.get(job.name) == key:
            return False
        self._last_slot[job.name] = key
        return True

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
        await refresh_leadership(self._ctx.leader)  # keep the lease alive / take over
        if not self._ctx.leader.is_leader():
            logger.info("ingest_skip_not_leader")
            return []
        local = self._local(now or datetime.now(UTC))
        today = local.date()
        ran: list[str] = []
        for job in self._jobs:
            if local.time() < job.fire_time:
                continue
            if job.cadence is Cadence.INTRADAY:
                if not self._intraday_slot_due(job, local):
                    continue
            elif not self._is_due(job, today):
                continue
            # Mark handled before running: a failure must not tight-loop within the window. The
            # next window's fire (or an explicit gap-fill) retries, matching the job's no-retry
            # contract.
            if job.cadence is not Cadence.INTRADAY:
                self._last_handled[job.name] = today
            run_date = today
            if self._is_us_daily(job):
                # US daily jobs run for the last completed US session, not the Taipei date: at
                # 06:00 Taipei the Taipei date's session has not opened yet (it asked Yahoo for
                # data that did not exist), and a Taipei Saturday/Sunday morning is exactly when
                # Friday's session lands, which the Taipei weekday gate below would have skipped.
                run_date = last_completed_us_session(self._ctx.us_calendar, now=local)
                if self._last_session.get(job.name) == run_date:
                    continue  # no new session since this job last ran (e.g. Sunday, Monday a.m.)
                self._last_session[job.name] = run_date
            # Monthly reference-data jobs are not tied to the market calendar (the classification
            # roster is published independent of trading sessions), so only daily market pulls are
            # trading-day-gated. Uses the extrapolating check (not the strict allow-list one) —
            # `today` is always beyond the calendar's known range until today's own job writes
            # its partition, so the strict check would skip every job every day forever; see
            # `TradingDayCalendar.is_trading_day_extrapolating`'s own docstring. Gates against
            # `job.market`'s own calendar (`ctx.us_calendar` for a US-scoped job, `ctx.calendar`
            # otherwise) — see `ScheduledIngestJob.market`'s own field comment for why this must
            # not default to the TW calendar for every job regardless of which market it serves.
            calendar = self._ctx.us_calendar if job.market is Market.US else self._ctx.calendar
            trading_gated = job.cadence in (Cadence.DAILY, Cadence.INTRADAY)
            if trading_gated and not calendar.is_trading_day_extrapolating(run_date):
                logger.info(
                    "ingest_job_skipped_non_trading_day",
                    job=job.name,
                    run_date=run_date.isoformat(),
                )
                continue
            try:
                result = await job.run(run_date, self._ctx)
            except FinMindQuotaRefusedError:
                # FinMind refused again after its quota window reset (a lapsed plan or token):
                # every later job in this pass would wait out the window and be refused too.
                logger.exception("ingest_pass_stopped_finmind_refused", job=job.name)
                break
            except Exception:
                # Isolate a job's failure so one bad pull never blocks the others in this pass;
                # already marked handled, so it retries next day rather than looping now.
                logger.exception("ingest_job_errored", job=job.name, run_date=run_date.isoformat())
                continue
            logger.info(
                "ingest_job_fired",
                job=job.name,
                run_date=run_date.isoformat(),
                status=result.status.value,
                rows_written=result.rows_written,
            )
            ran.append(job.name)
        return ran


def _build_calendar(store: ParquetStore, dataset: str, market: str) -> TradingDayCalendar:
    """The real trading-day calendar for `market`, built from `PRICE_EOD_JOB`'s (TW) or
    `job_us_price_eod`'s (US) own already-ingested partition dates — ground truth for "was this
    a real trading day" in that specific market, never a naive Mon-Fri weekday guess. A
    whole-market EOD job only ever writes a partition when its own calendar gate let a run
    through, and a market's real holiday calendar can't be predicted from weekday math alone
    (TW: 國慶日/農曆春節/清明/228; US: Thanksgiving/Juneteenth/etc.) — every one of those real
    holidays previously miscounted as a trading day silently starved every TW lookback window
    that assumes N calendar-weekdays == N trading days (see `analytics/scoring_batch/`'s
    `_PRICE_LOOKBACK_DAYS`, which needs ≥252 real trading days and was landing on as few as 239
    out of a 260-weekday window before this fix). TW and US calendars are built and kept separate
    (`IngestContext.calendar` vs `.us_calendar`) since their holiday sets diverge — a shared
    calendar would wrongly gate one market's jobs off the other's closures.

    Falls back to the plain Mon-Fri calendar (`TradingDayCalendar()`'s own documented
    degradation) only on a genuinely empty store for that market — a fresh volume with no
    partitions yet, where there is no ground truth to build from. Once even one whole-market
    ingest run has landed for that market, every subsequent process picks up the real calendar."""
    known_dates = store.list_partition_dates(dataset, market)
    if not known_dates:
        return TradingDayCalendar()
    return calendar_from_known_dates(known_dates)


async def build_ingest_context_from_settings(settings: Settings | None = None) -> IngestContext:
    """Assemble the job context from config — the FinMind adapter, the leader-only Parquet writer,
    the shared leader-election seam, and the trading-day calendar. No network I/O at construction.

    The batch-completion invalidation publisher (`data.updated`) is bound from config: the concrete
    cross-process transport lives in `events/`, which the ingest layer may not import under the
    one-way dependency rule, so `resolve_data_update_publisher` finds it by its configured
    `"module:callable"` name. With the in-memory bus, an empty setting, or any wiring problem it
    degrades to the seam's no-op — the partition still lands and clients fall back to
    boundary-anchored revalidation / the alert sweep.

    `async def` (not sync) so it can `await ensure_schema_for(...)` on the caller's own already-
    running startup loop — this project's sole schema-provisioning mechanism for the relational
    session factory below (the daily Parquet jobs never touch it, but the theme-seed job does).
    """
    resolved = settings or get_settings()
    session_factory = make_session_factory(make_engine(resolved.database_url))
    await ensure_schema_for(session_factory)
    store = ParquetStore(root=resolved.parquet_root)
    return IngestContext(
        # Counted against the same per-call cap as the API, limited to ingest's share of it.
        finmind=FinMindAdapter(resolved, quota=build_finmind_quota(resolved, role="ingest")),
        store=store,
        leader=build_leader_election(
            role="ingest-writer",
            database_url=resolved.database_url,
            scope_root=resolved.parquet_root,
        ),
        calendar=_build_calendar(store, PRICE_EOD_JOB.dataset, Market.TW.value),
        us_calendar=_build_calendar(store, US_PRICE_EOD_DATASET, Market.US.value),
        tls_policy=resolved.tls_policy,
        # The relational session factory the theme-seed job writes through — the daily Parquet
        # jobs never touch it. Built here from the same database URL the rest of the app uses.
        session_factory=session_factory,
        # Only the US whole-market jobs (`ingest/us_jobs.py`) use this.
        yfinance=YFinanceAdapter(),
        data_update_publisher=resolve_data_update_publisher(resolved),
    )


async def close_ingest_context(ctx: IngestContext) -> None:
    """Release what `build_ingest_context_from_settings` opened: the writer lease, then the
    database pool. Safe to call on every exit path, including an early abort."""
    await release_leadership(ctx.leader)
    factory = ctx.session_factory
    engine = factory.kw.get("bind") if factory is not None else None
    if engine is not None:
        await engine.dispose()


__all__ = [
    "SCHEDULED_JOBS",
    "Cadence",
    "IngestDaemon",
    "IngestJobFn",
    "ScheduledIngestJob",
    "build_ingest_context_from_settings",
    "close_ingest_context",
]
