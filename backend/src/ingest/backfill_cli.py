"""`python -m ingest.backfill_cli` — the operator's one-shot dev backfill trigger.

A separate module from `ingest/__main__.py` on purpose: that file's `__main__` guard belongs to
the long-running leader-scheduled daemon (`python -m ingest`), a different process shape and
different operational concern than this one-shot, interactive, exit-code-bearing tool. Keeping
them apart means neither concern has to reason about the other's argument parsing or exit
semantics.

This is the *explicit manual trigger* the platform's ingest carve-out already describes as
permitted: rather than requiring the operator to export `INGEST_ON_STARTUP=true` into the whole
process environment just to run one bounded gap-fill, this CLI accepts its own `--yes` override
and passes it straight into `run_preflight_if_enabled`'s existing `ingest_on_startup` parameter —
the same gate the dev-only startup catch-up uses, just supplied per-invocation instead of via env.
The one thing this CLI will never do is let that override paper over a production config: it
hard-stops unconditionally when `Settings.env == "production"`, regardless of `--yes`, before
touching any spec, so a mis-set override can never turn into an accidental prod backfill.

The `tw` path also runs `run_derived_compute_catchup` after the whole-market pulls land: the
score/screener batches are derived-compute passes over those pulls' own output, not
`WholeMarketJobSpec`s, so `run_preflight_if_enabled`'s per-spec gap-fill never reaches them — see
that function's own docstring for why a dev restart otherwise leaves them permanently stuck at
"not yet computed" until the next scheduled fire time.

`run_backfill_cli` also runs `_run_non_finmind_forward_only_jobs_for_today` — six more TW jobs
that are not `WholeMarketJobSpec`s either (`job_etf_nav`/`job_etf_aum`, the two MOPS jobs, the
two TWSE+TPEX 處置王-board jobs). A live audit this session found these six sitting stale for
days while `run_backfill_cli` reported a clean `failed_runs=0` — the CLI's own per-spec loop
simply never walks them, so a stale gap was invisible in its own summary. All six hit upstream
endpoints with no date parameter at all (confirmed live, one by one) — see that function's own
docstring for why they're called for today only, not walked back over `years` of history the way
FinMind specs are; the function is named for that forward-only constraint specifically, not
"non-FinMind" in general, since `job_etf_expense_ratio` below is also non-FinMind but does NOT
share that constraint.

A seventh non-`WholeMarketJobSpec` TW job, `job_etf_expense_ratio` (SITCA fee disclosure), is
walked separately by `_backfill_etf_expense_ratio` — unlike the six above, SITCA's own report
form genuinely accepts a past month (confirmed live this session: real per-fund rows for
non-latest months, not just "now"), so it gets a real monthly backfill over `years` with a
skip-if-already-ingested check, the same shape `_backfill_shareholding_distribution` uses for its
own weekly cadence.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import structlog

from analytics.supply_chain.verification.job import job_sc_research
from config.settings import Settings, get_settings
from ingest.context import IngestContext
from ingest.daemon import build_ingest_context_from_settings, close_ingest_context
from ingest.data_updates import CoalescingDataUpdatePublisher
from ingest.derived_catchup import catch_up_tw, catch_up_us
from ingest.jobs import (
    DAILY_WHOLE_MARKET_JOBS,
    ETF_EXPENSE_RATIO_DATASET,
    SHAREHOLDING_DISTRIBUTION_JOB,
    WholeMarketJobSpec,
    job_attention_stocks,
    job_disposition_events,
    job_etf_aum,
    job_etf_expense_ratio,
    job_etf_nav,
    job_insider_transfer_declaration,
    job_material_announcements,
    job_shareholding_distribution,
)
from ingest.journal import JobRun, RunStatus, journal_run
from ingest.leader import acquire_leadership
from ingest.preflight import (
    last_available_trading_day,
    last_completed_us_session,
    run_preflight_if_enabled,
    snapshot_day,
)
from ingest.us_jobs import backfill_us_price_range
from ingest.us_universe import job_us_symbol_universe
from sources.finmind import FinMindQuotaRefusedError

_NEEDS_YES_REASON = "INGEST_ON_STARTUP is false and --yes was not passed"

logger = structlog.get_logger(__name__)

# Exit status when another ingest process holds the single-writer lease (distinct from 1 = failed).
LEASE_HELD_EXIT_CODE = 3

_PRODUCTION_ENV = "production"
_MAX_BACKFILL_YEARS = 10  # generous upper bound — never an unbounded operator-supplied window


class ProductionHardStopError(RuntimeError):
    """Raised when the CLI is invoked against a production config — never caught, always fatal."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ingest.backfill_cli",
        description=(
            "One-shot dev backfill: gap-fill every whole-market job over the bounded "
            "dev window, through the same run_preflight_if_enabled() code path the "
            "INGEST_ON_STARTUP catch-up uses."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Explicit manual-trigger override: run even if INGEST_ON_STARTUP is not set in "
            "the environment. Still hard-refuses if ENV=production."
        ),
    )
    parser.add_argument(
        "--years",
        type=int,
        default=None,
        help="Override dev_backfill_years (small positive int; default from Settings).",
    )
    parser.add_argument(
        "--market",
        choices=("tw", "us"),
        default="tw",
        help=(
            "Which whole-market job group to backfill: 'tw' (default, FinMind-sourced "
            "ALL_WHOLE_MARKET_JOBS) or 'us' (yfinance-sourced price + fundamentals, "
            "ingest/us_jobs.py — requires the US symbol universe, refreshed first if stale)."
        ),
    )
    return parser.parse_args(argv)


def _validate_years(years: int | None, settings: Settings) -> int:
    resolved = settings.dev_backfill_years if years is None else years
    if resolved <= 0 or resolved > _MAX_BACKFILL_YEARS:
        raise ValueError(
            f"dev_backfill_years must be in [1, {_MAX_BACKFILL_YEARS}], got {resolved}"
        )
    return resolved


def _check_not_production(settings: Settings) -> None:
    if settings.env == _PRODUCTION_ENV:
        raise ProductionHardStopError(
            "refusing to run: ENV=production — this CLI never runs against a production "
            "config, regardless of --yes or INGEST_ON_STARTUP."
        )


async def run_backfill_cli(
    settings: Settings,
    ctx: IngestContext,
    *,
    override: bool,
    years: int,
    specs: Sequence[WholeMarketJobSpec] = DAILY_WHOLE_MARKET_JOBS,
) -> tuple[JobRun, ...]:
    """Run `run_preflight_if_enabled` once per spec, printing a per-dataset summary as each
    `JobRun` batch lands. One failing spec is logged and does not stop the remaining specs —
    mirroring `backfill_range`'s own per-date resumability philosophy.

    `specs` defaults to `DAILY_WHOLE_MARKET_JOBS` (every whole-market spec EXCEPT
    `SHAREHOLDING_DISTRIBUTION_JOB`) — that one dataset is weekly and only ever returns rows for
    the exact Friday publish date, so running it through this generic one-call-per-calendar-day
    loop wastes ~5 empty FinMind calls for every 1 real one across a multi-year window.
    `_backfill_shareholding_distribution` below covers it with the same weekly-aware search
    `job_shareholding_distribution` itself already uses for the daily cron path."""
    _check_not_production(settings)
    ingest_on_startup = override or settings.ingest_on_startup
    if not ingest_on_startup:
        logger.warning(
            "ingest_backfill_cli_refused",
            reason=_NEEDS_YES_REASON,
        )
        return ()

    all_runs: list[JobRun] = []
    refused: FinMindQuotaRefusedError | None = None
    for spec in specs:
        try:
            runs = await run_preflight_if_enabled(
                spec,
                ctx,
                ingest_on_startup=True,
                dev_backfill_years=years,
            )
        except FinMindQuotaRefusedError as exc:
            # Every later FinMind spec would wait out the window and be refused the same way, so
            # the FinMind pulls stop here; the work that does not use FinMind still runs below.
            refused = exc
            break
        except Exception:
            logger.exception("ingest_backfill_cli_spec_errored", dataset=spec.dataset)
            continue
        for run in runs:
            logger.info(
                "ingest_backfill_cli_run",
                dataset=run.dataset,
                run_date=run.run_date.isoformat(),
                rows_written=run.rows_written,
                status=run.status.value,
            )
        all_runs.extend(runs)

    if refused is None:
        all_runs.extend(await _backfill_shareholding_distribution(ctx, years=years))
    # The remaining jobs read other sources (SITCA, TWSE, MOPS, yfinance). The no-date-parameter
    # feeds can only be fetched as they stand today, so skipping them for a FinMind outage loses
    # that day for good.
    all_runs.extend(await _backfill_etf_expense_ratio(ctx, years=years))
    all_runs.extend(await _run_non_finmind_forward_only_jobs_for_today(ctx))
    if refused is not None:
        all_runs.append(
            journal_run(
                "finmind_quota",
                last_available_trading_day(ctx.calendar, now=datetime.now(UTC)),
                rows_written=0,
                status=RunStatus.FAILED,
                detail=str(refused),
            )
        )
    return tuple(all_runs)


async def _run_non_finmind_forward_only_jobs_for_today(ctx: IngestContext) -> tuple[JobRun, ...]:
    """Six TW jobs `DAILY_WHOLE_MARKET_JOBS`'s generic per-spec loop never reaches, run directly
    for TODAY only — the same direct-call treatment `ingest/__main__.py::_tw_gap_fill` already
    gives four of them (mirrored here so the CLI operator path and the daemon startup path can't
    silently drift apart). Named for the constraint every one of these six shares — their
    upstream endpoint has no date parameter at all, confirmed live one by one, so "today" is the
    only value that could ever be fetched — not "non-FinMind" in general: `job_etf_expense_ratio`
    is also non-FinMind but is walked separately by `_backfill_etf_expense_ratio` below, since
    its upstream (SITCA) genuinely does accept a past month.

    - `job_etf_nav`/`job_etf_aum` (TWSE/yfinance-sourced ETF NAV + AUM, not a `WholeMarketJobSpec`
      — confirmed missing from a live backfill run this session: both sat stale for days with
      `run_backfill_cli` reporting a clean `failed_runs=0` the whole time, since a spec it never
      walks can't be gap-filled by it). `job_etf_aum` depends on `job_etf_nav`'s own
      already-ingested `etf_nav` partition (`_etf_nav_rows_as_of`), so NAV must run first.
    - `job_material_announcements`/`job_insider_transfer_declaration` (MOPS) and
      `job_attention_stocks`/`job_disposition_events` (TWSE+TPEX) — all four forward-only, no
      date parameter upstream, so unlike every FinMind spec above there is no historical window
      to walk back over; today's call is the only backfill that is possible at all for a day
      this process was down at the original cron fire time.

    Deliberately NOT walked back over `years` of history the way
    `_backfill_shareholding_distribution` walks weeks: `job_etf_aum` fans out one yfinance call
    per ETF symbol (confirmed ~2-3 minutes for a single day against the current ~250-symbol
    roster live) — a multi-year daily walk would take hours and is not what an operator invoking
    this CLI for a routine gap-fill is asking for. `_tw_gap_fill`'s own "call unconditionally,
    idempotent" reasoning applies here too: `write_partition` always replaces the whole day's
    partition, so re-running for a day that already succeeded is a same-answer no-op."""
    today = snapshot_day(ctx.calendar, now=datetime.now(UTC))
    if today is None:
        logger.info("ingest_backfill_cli_snapshot_skipped", reason="not a trading day")
        return ()
    runs = (
        await job_etf_nav(today, ctx),
        await job_etf_aum(today, ctx),
        await job_material_announcements(today, ctx),
        await job_insider_transfer_declaration(today, ctx),
        await job_attention_stocks(today, ctx),
        await job_disposition_events(today, ctx),
    )
    for run in runs:
        logger.info(
            "ingest_backfill_cli_run",
            dataset=run.dataset,
            run_date=run.run_date.isoformat(),
            rows_written=run.rows_written,
            status=run.status.value,
        )
    return runs


async def _backfill_shareholding_distribution(
    ctx: IngestContext, *, years: int
) -> tuple[JobRun, ...]:
    """Gap-fill `SHAREHOLDING_DISTRIBUTION_JOB` — one call per WEEK (not per calendar day) over
    the same `[target - years*250 trading days, target]` window `run_preflight_if_enabled` uses
    for every other spec, calling `job_shareholding_distribution` directly so each week gets its
    own backward-search-for-the-exact-Friday-publish-date instead of the generic daily loop's
    ~5-empty-calls-per-1-real-one waste.

    `only_missing`-equivalent: a week already covered by a real, already-ingested partition is
    skipped without a FinMind call. `job_shareholding_distribution` writes under the RESOLVED
    publish date, not the `cursor` date passed in (a Fri publish date can resolve from a
    Sat/Sun/Mon cursor within `_WEEKLY_PUBLISH_SEARCH_DAYS`) — so the skip check is "does any
    known partition date fall within a week of `cursor`", not a single `has_partition(cursor)`
    lookup, which would always miss and re-fetch every already-covered week."""
    target = last_available_trading_day(ctx.calendar, now=datetime.now(UTC))
    start = ctx.calendar.shift_business_days(target, -(years * 250))
    known_dates = ctx.store.list_partition_dates(
        SHAREHOLDING_DISTRIBUTION_JOB.dataset, ctx.market.value
    )
    runs: list[JobRun] = []
    cursor = target
    while cursor >= start:
        window_start = cursor - timedelta(days=6)
        if any(window_start <= d <= cursor for d in known_dates):
            cursor -= timedelta(days=7)
            continue
        runs.append(await job_shareholding_distribution(cursor, ctx))
        cursor -= timedelta(days=7)
    return tuple(runs)


async def _backfill_etf_expense_ratio(ctx: IngestContext, *, years: int) -> tuple[JobRun, ...]:
    """Gap-fill `job_etf_expense_ratio` (SITCA fee disclosure) — one call per MONTH over
    `years` of history, unlike the six forward-only jobs `_run_non_finmind_forward_only_jobs_
    for_today` limits to "today" only: SITCA's own report form genuinely accepts a past month
    (confirmed live this session — real per-fund rows for non-latest months, not just "now"), so
    a real historical walk is possible here, not just a today-only snapshot.

    `only_missing`-equivalent: a month already covered by a real, already-ingested partition is
    skipped without a network call — mirrors `_backfill_shareholding_distribution`'s own
    skip-if-known-date check, just keyed by calendar month instead of week. `job_etf_expense_
    ratio` writes under `run_date` directly (unlike the weekly job above, no separate "resolved
    publish date" to search for) and calendar-gates on `is_trading_day_extrapolating`, so the
    1st of the month is resolved forward to the month's first real trading day via
    `next_trading_day` before the job is called — the 1st itself may be a weekend/holiday, which
    the job would otherwise silently SKIP rather than fetch."""
    target = last_available_trading_day(ctx.calendar, now=datetime.now(UTC))
    start = ctx.calendar.shift_business_days(target, -(years * 250))
    known_dates = ctx.store.list_partition_dates(ETF_EXPENSE_RATIO_DATASET, ctx.market.value)
    known_months = {(d.year, d.month) for d in known_dates}
    runs: list[JobRun] = []
    cursor = target.replace(day=1)
    start_month = start.replace(day=1)
    while cursor >= start_month:
        if (cursor.year, cursor.month) not in known_months:
            run_date = ctx.calendar.next_trading_day(cursor, inclusive=True)
            runs.append(await job_etf_expense_ratio(run_date, ctx))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    return tuple(runs)


async def run_derived_compute_catchup(
    settings: Settings,
    ctx: IngestContext,
    *,
    override: bool,
    years: int,
) -> tuple[JobRun, ...]:
    """The derived-compute counterpart to `run_backfill_cli`: `job_scoring_batch` and
    `job_screener_flags` are not `WholeMarketJobSpec`s (they read back the partitions the whole-
    market pulls above just wrote, rather than pulling from FinMind themselves), so
    `run_preflight_if_enabled`'s own gap-fill never reaches them — a dev box that was down at
    their 23:55/20:30 Taipei fire time permanently skips that day's compute with no catch-up,
    unlike every whole-market pull.

    Runs for EVERY trading day in `[target - years*250 trading days, target]` missing a
    `screener_flags` partition (mirroring `ingest/backfill.py::detect_missing_trading_days`'s own
    gap-diff) — not just the single most recent day. A dev box down across a multi-day window
    (e.g. a long weekend plus a rebuild) needs every one of those days computed, not only the
    latest; both jobs are idempotent (`upsert_score` replaces the row, `write_partition`
    overwrites the partition), so re-running for a day whose compute already landed is a
    same-answer no-op, not a correctness risk."""
    _check_not_production(settings)
    ingest_on_startup = override or settings.ingest_on_startup
    if not ingest_on_startup:
        logger.warning(
            "ingest_backfill_cli_refused",
            reason=_NEEDS_YES_REASON,
        )
        return ()

    target = last_available_trading_day(ctx.calendar, now=datetime.now(UTC))
    start = ctx.calendar.shift_business_days(target, -(years * 250))
    runs = await catch_up_tw(ctx, ctx.calendar.business_days_between(start, target))
    for run in runs:
        logger.info(
            "ingest_backfill_cli_run",
            dataset=run.dataset,
            run_date=run.run_date.isoformat(),
            rows_written=run.rows_written,
            status=run.status.value,
        )
    return tuple(runs)


async def run_us_backfill_cli(
    settings: Settings,
    ctx: IngestContext,
    *,
    override: bool,
    years: int,
) -> tuple[JobRun, ...]:
    """The US (yfinance) counterpart to `run_backfill_cli` — refreshes the symbol universe
    first (a stale/never-run universe means the price backfill below would have nothing to
    fetch), then gap-fills `job_us_price_eod` over the same bounded `dev_backfill_years` window
    the TW path uses (`backfill_us_price_range`, `only_missing=True`). The weekly fundamentals
    job is intentionally NOT run here: unlike price, a "backfill fundamentals" request is a
    single current-snapshot pull, not a historical-date-range pull (yfinance's financials/
    dividends/splits calls always return each symbol's own available history, not a
    caller-scoped date range) — an operator wanting fundamentals runs `job_us_fundamentals`
    directly, or waits for its own weekly schedule; this CLI's job is filling the *date-range*
    gap only price EOD actually has."""
    _check_not_production(settings)
    ingest_on_startup = override or settings.ingest_on_startup
    if not ingest_on_startup:
        logger.warning(
            "ingest_backfill_cli_refused",
            reason=_NEEDS_YES_REASON,
        )
        return ()

    universe_run = await job_us_symbol_universe(datetime.now(UTC).date(), ctx)
    logger.info(
        "ingest_backfill_cli_run",
        dataset=universe_run.dataset,
        run_date=universe_run.run_date.isoformat(),
        rows_written=universe_run.rows_written,
        status=universe_run.status.value,
    )
    if universe_run.status is RunStatus.FAILED:
        return (universe_run,)

    target = last_completed_us_session(ctx.us_calendar, now=datetime.now(UTC))
    start = ctx.us_calendar.shift_business_days(target, -(years * 250))
    logger.info(
        "ingest_backfill_cli_started",
        market="us",
        start=start.isoformat(),
        target=target.isoformat(),
        dev_backfill_years=years,
    )
    price_runs = await backfill_us_price_range(ctx, start=start, end=target, only_missing=True)
    score_runs = await catch_up_us(
        ctx, ctx.us_calendar.business_days_between(start, target), target=target
    )
    for run in (*price_runs, *score_runs):
        logger.info(
            "ingest_backfill_cli_run",
            dataset=run.dataset,
            run_date=run.run_date.isoformat(),
            rows_written=run.rows_written,
            status=run.status.value,
        )
    return (universe_run, *price_runs, *score_runs)


async def _amain(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    try:
        _check_not_production(settings)
        years = _validate_years(args.years, settings)
    except (ProductionHardStopError, ValueError) as exc:
        logger.error("ingest_backfill_cli_aborted", detail=str(exc))
        return 1

    built = await build_ingest_context_from_settings(settings)
    # One `data.updated` per dataset at the end, at its newest date, instead of one per partition
    # written (see `CoalescingDataUpdatePublisher`).
    signals = CoalescingDataUpdatePublisher(built.data_update_publisher)
    ctx: IngestContext = dataclasses.replace(built, data_update_publisher=signals)
    # A hand-run backfill is a second writer like any other: it takes the same lease as the daemon,
    # so it can never interleave with the scheduled jobs on the same partitions.
    try:
        return await _run_with_lease(args, settings, ctx, years)
    except FinMindQuotaRefusedError as exc:
        logger.error("ingest_backfill_cli_aborted", detail=str(exc))
        return 1
    finally:
        await signals.flush()
        await close_ingest_context(ctx)


async def _run_with_lease(
    args: argparse.Namespace, settings: Settings, ctx: IngestContext, years: int
) -> int:
    if not await acquire_leadership(ctx.leader):
        logger.error(
            "ingest_backfill_cli_aborted",
            detail=(
                "another ingest process (the daemon or a second backfill) holds the writer lease; "
                "stop it or wait for it to finish, then run this again"
            ),
        )
        return LEASE_HELD_EXIT_CODE
    return await _run_backfill(args, settings, ctx, years)


async def _run_backfill(
    args: argparse.Namespace, settings: Settings, ctx: IngestContext, years: int
) -> int:
    if args.market == "us":
        runs = await run_us_backfill_cli(settings, ctx, override=args.yes, years=years)
    else:
        runs = await run_backfill_cli(settings, ctx, override=args.yes, years=years)
        runs += await run_derived_compute_catchup(settings, ctx, override=args.yes, years=years)
        runs += await _optional_sc_research(settings, ctx)
    if not runs:
        logger.info("ingest_backfill_cli_no_runs")
        return 0
    failed = tuple(r for r in runs if r.status is RunStatus.FAILED)
    logger.info(
        "ingest_backfill_cli_summary",
        total_runs=len(runs),
        failed_runs=len(failed),
    )
    return 1 if failed else 0


async def _optional_sc_research(settings: Settings, ctx: IngestContext) -> tuple[JobRun, ...]:
    """Optional dev step: one budgeted supply-chain research pass, only when
    SUPPLY_CHAIN_RESEARCH_ENABLED is set (the job re-checks the leader, key and LLM gates)."""
    if not settings.supply_chain_research_enabled:
        return ()
    return (await job_sc_research(datetime.now(UTC).date(), ctx),)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ProductionHardStopError",
    "main",
    "parse_args",
    "run_backfill_cli",
    "run_derived_compute_catchup",
    "run_us_backfill_cli",
]
