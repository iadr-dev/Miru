"""Weekly shareholding-distribution (大戶持股) job."""

from __future__ import annotations

from datetime import date as date_
from datetime import timedelta
from typing import cast

import structlog

from ingest.context import IngestContext
from ingest.jobs.specs import SHAREHOLDING_DISTRIBUTION_JOB
from ingest.jobs.whole_market import fetch_error_status as _fetch_error_status
from ingest.jobs.whole_market import validate_rows as _validate_rows
from ingest.journal import JobRun, RunStatus, journal_run

logger = structlog.get_logger(__name__)


# How many trading days back `job_shareholding_distribution` searches for the week's real
# publish date before giving up — confirmed live that FinMind only returns rows for the exact
# Friday `start_date` (any other weekday in that week returns zero rows), and a market holiday
# can push the week's actual last trading day earlier than Friday. 5 covers a full trading week
# plus one day of slack for an unusually early holiday-shortened week.
_WEEKLY_PUBLISH_SEARCH_DAYS = 5


_ONE_DAY = timedelta(days=1)


async def job_shareholding_distribution(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-weekly` — TDCC/FinMind 股權持股分級表 (`SHAREHOLDING_DISTRIBUTION_JOB`), cron
    **06:00 weekly** (`Cadence.WEEKLY` in `ingest/daemon.py`, same cadence family as
    `job_us_fundamentals`).

    Unlike every other `run_whole_market_job` caller, this dataset's upstream `start_date` is
    not "any trading day, possibly empty" — confirmed live it must be the EXACT date the week's
    snapshot was published (every other day in that week returns zero rows, not a smaller/
    partial result). Since the daemon always calls this with `today` (whatever day its cadence
    gate let through, not necessarily the publish day itself), this function searches backward
    from `run_date` for the first date that actually has rows, rather than trusting `run_date`
    directly the way a daily-cadence job safely can.

    Idempotent and safe to re-run: `ParquetStore.write_partition` is keyed by the *resolved*
    publish date, not `run_date` — a re-run that resolves the same publish date overwrites the
    same partition rather than duplicating it."""
    if not ctx.leader.is_leader():
        return journal_run(
            SHAREHOLDING_DISTRIBUTION_JOB.dataset,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )
    candidate = run_date
    for _ in range(_WEEKLY_PUBLISH_SEARCH_DAYS):
        result = await ctx.finmind.fetch_whole_market(
            SHAREHOLDING_DISTRIBUTION_JOB.capability,
            tls_policy=ctx.tls_policy,
            start_date=candidate.isoformat(),
        )
        if _fetch_error_status(result) is RunStatus.SUCCESS:
            row_key = next(iter(result.payload), None)
            rows_obj: object = result.payload.get(row_key, []) if row_key else []
            raw_rows: list[dict[str, object]] = (
                cast("list[dict[str, object]]", rows_obj) if isinstance(rows_obj, list) else []
            )
            if raw_rows:
                rows = _validate_rows(SHAREHOLDING_DISTRIBUTION_JOB, raw_rows, candidate)
                rows_written = ctx.store.write_partition(
                    SHAREHOLDING_DISTRIBUTION_JOB.dataset,
                    ctx.market.value,
                    candidate,
                    rows,
                )
                await ctx.data_update_publisher.publish_data_updated(
                    capability=SHAREHOLDING_DISTRIBUTION_JOB.capability,
                    market=ctx.market,
                    as_of=candidate.isoformat(),
                    scope="whole-market",
                )
                return journal_run(
                    SHAREHOLDING_DISTRIBUTION_JOB.dataset,
                    candidate,
                    rows_written=rows_written,
                    status=RunStatus.SUCCESS,
                    detail=f"resolved publish date {candidate.isoformat()} (searched back "
                    f"from {run_date.isoformat()})",
                )
        candidate -= _ONE_DAY
    logger.warning(
        "ingest_weekly_publish_date_not_found",
        dataset=SHAREHOLDING_DISTRIBUTION_JOB.dataset,
        run_date=run_date.isoformat(),
        searched_days=_WEEKLY_PUBLISH_SEARCH_DAYS,
    )
    return journal_run(
        SHAREHOLDING_DISTRIBUTION_JOB.dataset,
        run_date,
        rows_written=0,
        status=RunStatus.SKIPPED,
        detail=f"no publish found within {_WEEKLY_PUBLISH_SEARCH_DAYS} days back from "
        f"{run_date.isoformat()}",
    )
