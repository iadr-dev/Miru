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
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import structlog

from analytics.scoring_batch import job_scoring_batch
from analytics.screener.batch import SCREENER_FLAGS_DATASET, job_screener_flags
from config.settings import Settings, get_settings
from ingest.context import IngestContext
from ingest.daemon import build_ingest_context_from_settings
from ingest.jobs import (
    DAILY_WHOLE_MARKET_JOBS,
    SHAREHOLDING_DISTRIBUTION_JOB,
    WholeMarketJobSpec,
    job_shareholding_distribution,
)
from ingest.jobs import WEEKLY_PUBLISH_SEARCH_DAYS as _SHAREHOLDING_SEARCH_DAYS
from ingest.journal import JobRun, RunStatus
from ingest.preflight import last_available_trading_day, run_preflight_if_enabled
from ingest.us_jobs import backfill_us_price_range
from ingest.us_universe import job_us_symbol_universe

logger = structlog.get_logger(__name__)

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

    `specs` defaults to `DAILY_WHOLE_MARKET_JOBS` (every whole-market job EXCEPT the weekly
    `SHAREHOLDING_DISTRIBUTION_JOB`) — that one dataset's own weekly publish-date-search control
    flow doesn't fit the generic "one date per calendar day in range" sweep every other spec
    here goes through, so it is backfilled separately, right after this generic loop, via
    `_backfill_shareholding_distribution`."""
    _check_not_production(settings)
    ingest_on_startup = override or settings.ingest_on_startup
    if not ingest_on_startup:
        logger.warning(
            "ingest_backfill_cli_refused",
            reason="INGEST_ON_STARTUP is false and --yes was not passed",
        )
        return ()

    all_runs: list[JobRun] = []
    for spec in specs:
        try:
            runs = await run_preflight_if_enabled(
                spec,
                ctx,
                ingest_on_startup=True,
                dev_backfill_years=years,
            )
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

    all_runs.extend(await _backfill_shareholding_distribution(ctx, years=years))
    return tuple(all_runs)


async def _backfill_shareholding_distribution(
    ctx: IngestContext, *, years: int
) -> tuple[JobRun, ...]:
    """The weekly counterpart to the generic per-calendar-day sweep above, for
    `SHAREHOLDING_DISTRIBUTION_JOB` — `job_shareholding_distribution` resolves its own real
    publish date via a backward search (see that function's own docstring), so this walks
    backward one week at a time from `last_available_trading_day` over the same bounded
    `years`-sized window the other jobs use, calling the job once per week rather than once per
    calendar day.

    Skips a week whose resolved publish-date partition already exists — checked via
    `ctx.store.list_partition_dates`, which only reports partitions with real (non-empty) data,
    never a placeholder — so a re-run over an already-covered window makes no FinMind calls at
    all. The check must be against the set of RESOLVED dates already on disk, not the weekly
    cursor itself: the cursor and the dataset's own resolved publish date (a Friday) can differ
    by up to `WEEKLY_PUBLISH_SEARCH_DAYS` days, so a naive `has_partition(cursor)` check would
    never match and would re-fetch every already-covered week."""
    spec = SHAREHOLDING_DISTRIBUTION_JOB
    known_dates = set(ctx.store.list_partition_dates(spec.dataset, ctx.market.value))
    target = last_available_trading_day(ctx.calendar, now=datetime.now(UTC))
    start = ctx.calendar.shift_business_days(target, -(years * 250))

    runs: list[JobRun] = []
    cursor = start
    while cursor <= target:
        # A week is "already covered" if ANY known resolved-publish-date partition falls within
        # the job's own backward-search window behind this cursor — the same window
        # `job_shareholding_distribution` itself searches, so this skip check and that search
        # agree on what counts as "this week".
        already_covered = any(
            cursor - timedelta(days=_SHAREHOLDING_SEARCH_DAYS) <= known <= cursor
            for known in known_dates
        )
        if not already_covered:
            run = await job_shareholding_distribution(cursor, ctx)
            runs.append(run)
            logger.info(
                "ingest_backfill_cli_run",
                dataset=run.dataset,
                run_date=run.run_date.isoformat(),
                rows_written=run.rows_written,
                status=run.status.value,
            )
        cursor += timedelta(weeks=1)
    return tuple(runs)


async def run_derived_compute_catchup(
    settings: Settings,
    ctx: IngestContext,
    *,
    override: bool,
    years: int = 1,
) -> tuple[JobRun, ...]:
    """The derived-compute counterpart to `run_backfill_cli`: `job_scoring_batch` and
    `job_screener_flags` are not `WholeMarketJobSpec`s (they read back the partitions the whole-
    market pulls above just wrote, rather than pulling from FinMind themselves), so
    `run_preflight_if_enabled`'s own gap-fill never reaches them — a dev box that was down at
    their 23:55/20:30 Taipei fire time permanently skips that day's compute with no catch-up,
    unlike every whole-market pull.

    Walks every trading day in `[target - years*250 trading days, target]` and computes both
    jobs for each one that is still missing its `screener_flags` partition — not just the single
    most recent day — so a dev box down across a multi-day gap (a long weekend plus a rebuild)
    gets every in-between day covered, not permanently stuck once the underlying whole-market
    data for it is later backfilled. Both jobs are idempotent (`upsert_score` replaces the row,
    `write_partition` overwrites the partition), so re-running for an already-computed day is a
    same-answer no-op, not a correctness risk — the missing-partition check is purely to avoid
    needless recompute over the whole window on every catch-up run."""
    _check_not_production(settings)
    ingest_on_startup = override or settings.ingest_on_startup
    if not ingest_on_startup:
        logger.warning(
            "ingest_backfill_cli_refused",
            reason="INGEST_ON_STARTUP is false and --yes was not passed",
        )
        return ()

    target = last_available_trading_day(ctx.calendar, now=datetime.now(UTC))
    start = ctx.calendar.shift_business_days(target, -(years * 250))

    runs: list[JobRun] = []
    for day in ctx.calendar.business_days_between(start, target):
        if ctx.store.has_partition(SCREENER_FLAGS_DATASET, ctx.market.value, day):
            continue
        day_runs = (
            await job_screener_flags(day, ctx),
            await job_scoring_batch(day, ctx),
        )
        for run in day_runs:
            logger.info(
                "ingest_backfill_cli_run",
                dataset=run.dataset,
                run_date=run.run_date.isoformat(),
                rows_written=run.rows_written,
                status=run.status.value,
            )
        runs.extend(day_runs)
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
            reason="INGEST_ON_STARTUP is false and --yes was not passed",
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

    target = last_available_trading_day(ctx.calendar, now=datetime.now(UTC))
    start = ctx.calendar.shift_business_days(target, -(years * 250))
    logger.info(
        "ingest_backfill_cli_started",
        market="us",
        start=start.isoformat(),
        target=target.isoformat(),
        dev_backfill_years=years,
    )
    price_runs = await backfill_us_price_range(ctx, start=start, end=target, only_missing=True)
    for run in price_runs:
        logger.info(
            "ingest_backfill_cli_run",
            dataset=run.dataset,
            run_date=run.run_date.isoformat(),
            rows_written=run.rows_written,
            status=run.status.value,
        )
    return (universe_run, *price_runs)


async def _amain(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    try:
        _check_not_production(settings)
        years = _validate_years(args.years, settings)
    except (ProductionHardStopError, ValueError) as exc:
        logger.error("ingest_backfill_cli_aborted", detail=str(exc))
        return 1

    ctx = await build_ingest_context_from_settings(settings)
    if args.market == "us":
        runs = await run_us_backfill_cli(settings, ctx, override=args.yes, years=years)
    else:
        runs = await run_backfill_cli(settings, ctx, override=args.yes, years=years)
        runs += await run_derived_compute_catchup(settings, ctx, override=args.yes, years=years)
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
