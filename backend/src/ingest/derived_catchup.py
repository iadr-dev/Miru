"""Derived-compute catch-up: score and screen the trading days whose nightly run never happened.

`job_screener_flags` and `job_scoring_batch` are not whole-market pulls, so no gap-fill reaches
them: a box that was down at 20:30 / 23:55 (TW) or 07:30 (US) would skip that day for good. The
daily dev backfill (`ingest/backfill_cli.py`, which walks a long window) and the daemon itself
(`ingest/daemon.py`, which checks a short recent window once a day, so production heals without
an operator) both run the code here.

A day is a gap when it has no stored result: scores are read from the `stock_scores` table,
flags from their Parquet partition. The jobs skip a day whose price data has not landed, so
catching up such a day is a cheap no-op that is retried next pass, never a result computed from
older prices.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import date as date_

import structlog

from analytics.scoring_batch import job_scoring_batch
from analytics.scoring_store import scored_dates
from analytics.screener.batch import SCREENER_FLAGS_DATASET, job_screener_flags
from ingest.context import IngestContext
from ingest.journal import JobRun
from schemas.enums import Market

logger = structlog.get_logger(__name__)


async def days_without_scores(
    ctx: IngestContext, days: Sequence[date_], *, fallback: set[date_]
) -> set[date_]:
    """Trading days in `days` that have no stored scores for `ctx.market`, checked against
    `stock_scores` itself (keying it on `screener_flags` missed a day whose screener landed but
    whose scoring never ran). Only days from the first scored day onwards count, so a history that
    starts later is not re-run every time; with no database or no scores yet, `fallback` is used."""
    if ctx.session_factory is None:
        return fallback
    async with ctx.session_factory() as session:
        scored = await scored_dates(session, market=ctx.market)
    if not scored:
        return fallback
    first = min(scored)
    return {d for d in days if d >= first and d not in scored}


async def catch_up_tw(
    ctx: IngestContext, days: Sequence[date_], *, refresh: Sequence[date_] = ()
) -> tuple[JobRun, ...]:
    """Screen and score every TW day in `days` that is missing either result.

    `refresh` days are scored again even when they have scores: the evening run (or a backfill
    before 23:30) scores a day before the market-value data lands, and nothing else redoes it."""
    missing_flags = {
        d for d in days if not ctx.store.has_partition(SCREENER_FLAGS_DATASET, ctx.market.value, d)
    }
    missing_scores = await days_without_scores(ctx, days, fallback=missing_flags)
    missing_scores |= {d for d in refresh if d in days}
    runs: list[JobRun] = []
    for run_date in sorted(missing_flags | missing_scores):
        if run_date in missing_flags:
            runs.append(await job_screener_flags(run_date, ctx))
        if run_date in missing_scores:
            runs.append(await job_scoring_batch(run_date, ctx))
    return tuple(runs)


async def catch_up_us(
    ctx: IngestContext, days: Sequence[date_], *, target: date_
) -> tuple[JobRun, ...]:
    """Score every US session in `days` the 07:30 Taipei `us_scoring_batch` missed. A box with no
    US scores yet scores only `target`, the latest session."""
    us_ctx = dataclasses.replace(ctx, market=Market.US)
    missing = await days_without_scores(us_ctx, days, fallback={target})
    return tuple([await job_scoring_batch(run_date, us_ctx) for run_date in sorted(missing)])


__all__ = ["catch_up_tw", "catch_up_us", "days_without_scores"]
