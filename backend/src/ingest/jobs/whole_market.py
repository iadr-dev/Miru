"""The shared runner behind the whole-market jobs, and their one-line job functions."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date as date_

import structlog
from pydantic import BaseModel, ValidationError

from ingest.context import IngestContext
from ingest.jobs.period_dates import filing_date_for
from ingest.jobs.source_chain import MARKET_CLOSED_DETAIL, ChainOutcome, result_rows, walk_chain
from ingest.jobs.specs import (
    ACTIVE_ETF_HOLDING_CHANGE_JOB,
    ACTIVE_ETF_HOLDING_JOB,
    BALANCE_SHEET_JOB,
    CASH_FLOW_JOB,
    DIVIDENDS_JOB,
    FINANCIALS_JOB,
    INSTITUTIONAL_FLOW_JOB,
    MARGIN_SHORT_JOB,
    MARKET_VALUE_JOB,
    MARKET_VALUE_WEIGHT_JOB,
    MONTH_REVENUE_JOB,
    PRICE_EOD_JOB,
    STOCK_INFO_JOB,
    VALUATION_RATIOS_JOB,
    WholeMarketJobSpec,
)
from ingest.journal import JobRun, RunStatus, journal_run
from schemas.source import HealthStatus, SourceResult

logger = structlog.get_logger(__name__)


def fetch_error_status(result: SourceResult) -> RunStatus:
    if result.health.status in (HealthStatus.OK, HealthStatus.STALE):
        # STALE-with-no-exception still means "the call succeeded, empty body" — a legitimate
        # empty trading day is SUCCESS with rows=0, not an error.
        return RunStatus.SUCCESS
    return RunStatus.FAILED


def _run_if_trading_day(
    calendar: TradingDayCalendar, run_date: date_, dataset: str
) -> JobRun | None:
    """Calendar gate: return a SKIPPED journal entry and do nothing else if `run_date` is not
    a trading day — no fetch is even attempted. Uses the extrapolating check (not the strict
    allow-list one) since `run_date` is almost always "today", which is always beyond the
    calendar's known range until this very call writes its own partition — see
    `TradingDayCalendar.is_trading_day_extrapolating`'s own docstring."""
    if calendar.is_trading_day_extrapolating(run_date):
        return None
    return journal_run(
        dataset,
        run_date,
        rows_written=0,
        status=RunStatus.SKIPPED,
        detail="non-trading day (calendar-gated)",
    )


def validate_rows_against(
    row_model: type[BaseModel],
    dataset: str,
    raw_rows: list[dict[str, object]],
    run_date: date_,
) -> list[dict[str, object]]:
    """Validate every raw row against `row_model` BEFORE it ever reaches `write_partition` — the
    one ingest chokepoint every whole-market dataset passes through, so a malformed upstream row
    (a `None`/missing required field the upstream's raw JSON happens to carry for one row of one
    day) is dropped and logged here, never persisted to Parquet to crash a reader days or months
    later (see `WholeMarketJobSpec.row_model`'s own docstring for the real incident this fixes).
    A dropped row is not silently lost — `ingest_row_validation_failed` is logged per row with
    the dataset/date/error, and the journal's `rows_written` count already reflects the
    post-filter length, so a systematic upstream schema change becomes visible as a sudden drop
    in written-row counts, not a silent corruption."""
    valid_rows: list[dict[str, object]] = []
    for raw_row in raw_rows:
        try:
            row_model.model_validate(raw_row)
        except ValidationError as exc:
            logger.warning(
                "ingest_row_validation_failed",
                dataset=dataset,
                run_date=run_date.isoformat(),
                row_model=row_model.__name__,
                error=str(exc),
            )
            continue
        valid_rows.append(raw_row)
    return valid_rows


def validate_rows(
    spec: WholeMarketJobSpec, raw_rows: list[dict[str, object]], run_date: date_
) -> list[dict[str, object]]:
    """`validate_rows_against` bound to a `WholeMarketJobSpec`'s own `(row_model, dataset)` —
    the FinMind-sourced call sites' convenience wrapper."""
    return validate_rows_against(spec.row_model, spec.dataset, raw_rows, run_date)


def _failure_detail(outcome: ChainOutcome) -> str:
    """The journal reason of a day no source served. With a single source this is that source's
    own reason, unchanged; with several, every source's reason, so the journal shows why the
    official report failed as well as why the fallback did."""
    if len(outcome.chain_ids) == 1 and outcome.last is not None:
        return outcome.last.health.detail
    return f"no source served: {outcome.attempts_note()}"


def _success_detail(outcome: ChainOutcome, result: SourceResult) -> str:
    """The journal detail of a written partition: which source served it. A single-source chain
    keeps the source's own detail untouched; otherwise it is named, and a fallback says why the
    earlier sources did not serve."""
    if len(outcome.chain_ids) == 1:
        return result.health.detail
    if outcome.winner_id is None:  # every source healthy and empty
        return f"no rows from {', '.join(outcome.chain_ids)}"
    detail = f"served by {outcome.winner_id}: {result.health.detail}"
    if outcome.served_incomplete:
        detail += f" (no complete source: {outcome.attempts_note()})"
    elif outcome.fell_back:
        detail += f" (after {outcome.attempts_note()})"
    return detail


def _unserved_run(
    spec: WholeMarketJobSpec, run_date: date_, outcome: ChainOutcome
) -> JobRun | None:
    """The journal entry of a day no source served, `None` when every source answered healthy
    (a legitimately empty day, written by the caller).

    A period whose filing deadline has not passed is SKIPPED, not FAILED: no source can have the
    whole period yet (and a source that is down is no news). Nothing is written and NO marker is
    left, so an `only_missing` run asks again every time and picks the period up once the deadline
    is behind us; unlike a closure this is not final. Anything else that failed is FAILED, and the
    next scheduled run (or an explicit gap-fill) retries it."""
    if outcome.not_due is not None:
        return journal_run(
            spec.dataset, run_date, rows_written=0, status=RunStatus.SKIPPED, detail=outcome.not_due
        )
    if not outcome.failures:
        return None
    detail = _failure_detail(outcome)
    logger.warning(
        "ingest_whole_market_fetch_failed",
        dataset=spec.dataset,
        run_date=run_date.isoformat(),
        detail=detail,
    )
    return journal_run(
        spec.dataset, run_date, rows_written=0, status=RunStatus.FAILED, detail=detail
    )


def _holds_only_a_placeholder(
    ctx: IngestContext, spec: WholeMarketJobSpec, run_date: date_
) -> bool:
    store, market = ctx.store, ctx.market.value
    return store.has_partition(spec.dataset, market, run_date) and not store.has_rows(
        spec.dataset, market, run_date
    )


def _nominal_date(spec: WholeMarketJobSpec, run_date: date_, ctx: IngestContext) -> date_ | None:
    """For a period-keyed dataset whose period is stored under `run_date`, the nominal date the
    sources are asked for (the 1st, the quarter end), which differs from `run_date` when that date
    is not a trading day; `None` otherwise (the sources are asked for `run_date` itself, and a date
    that is no period's partition is answered "nothing by definition")."""
    if spec.period_anchor is None:
        return None
    return filing_date_for(spec.period_anchor, run_date, ctx.calendar)


async def run_whole_market_job(
    spec: WholeMarketJobSpec,
    run_date: date_,
    ctx: IngestContext,
) -> JobRun:
    """Run one whole-market-by-date ingest pass for `spec` on `run_date`.

    Idempotent: re-running for the same `(spec.capability, market, run_date)` overwrites the
    same partition version rather than duplicating rows — a write always rewrites the whole
    date partition. Safe to call from a cron, a gap-fill sweep, or a backfill loop — this is
    the one code path all three reuse.
    """
    skipped = _run_if_trading_day(ctx.calendar, run_date, spec.dataset)
    if skipped is not None:
        return skipped

    if not ctx.leader.is_leader():
        # Leadership contract: only the leader ingest process writes. Not holding leadership
        # is not an error — the leader (or the next election) will cover this date; no fetch
        # is attempted either, to avoid pointless quota spend.
        return journal_run(
            spec.dataset,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    date_str = run_date.isoformat()
    # The sources are asked in the configured order (official exchange reports, then FinMind);
    # the first healthy answer with rows wins. Never retry-loop tightly - a day no source could
    # serve journals FAILED and the next scheduled run (or an explicit gap-fill) retries.
    outcome = await walk_chain(
        ctx, spec.capability, run_date, source_date=_nominal_date(spec, run_date, ctx)
    )
    if outcome.market_closed:
        # Both exchanges said, explicitly, that nothing was published for a past date: a closure
        # the internal calendar could not know. Nothing is written and FinMind is not asked; the
        # marker keeps `only_missing` runs from re-fetching it (a failed day leaves none).
        ctx.store.mark_closed(spec.dataset, ctx.market.value, run_date, MARKET_CLOSED_DETAIL)
        return journal_run(
            spec.dataset,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=MARKET_CLOSED_DETAIL,
        )
    if outcome.winner is None:
        unserved = _unserved_run(spec, run_date, outcome)
        if unserved is not None:
            return unserved

    # A winner, or every source answered healthy with nothing (a legitimate empty trading day):
    # the latter writes the empty partition from the last answer, as a single source always did.
    result = outcome.winner or outcome.last
    if result is None:  # pragma: no cover - a chain always has at least one link
        raise RuntimeError(f"no whole-market source for {spec.dataset}")
    rows = validate_rows(spec, result_rows(result), run_date)
    if not rows and _holds_only_a_placeholder(ctx, spec, run_date):
        # Empty again for a date that already holds an empty placeholder: nothing would change,
        # so no new version is written and no signal sent. A backfill sweeps every such date of
        # a period dataset (monthly revenue is filed under one date a month) on each run.
        return journal_run(
            spec.dataset,
            run_date,
            rows_written=0,
            status=RunStatus.SUCCESS,
            detail=_success_detail(outcome, result),
        )
    rows_written = ctx.store.write_partition(spec.dataset, ctx.market.value, run_date, rows)
    ctx.store.clear_closed(spec.dataset, ctx.market.value, run_date)  # data arrived after all
    # The partition has landed atomically — signal it so a subscribed client revalidates exactly
    # the affected keys now, rather than waiting out a fixed time boundary. Leader-only: this line
    # is already inside the `is_leader()` guard above, so a follower never emits. Best-effort by
    # contract; a transport hiccup never rolls back the write that already succeeded.
    await ctx.data_update_publisher.publish_data_updated(
        capability=spec.capability,
        market=ctx.market,
        as_of=date_str,
        scope="whole-market",
    )
    return journal_run(
        spec.dataset,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=_success_detail(outcome, result),
    )


async def job_price_eod(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-postclose` — EOD OHLCV, cron **17:40**."""
    return await run_whole_market_job(PRICE_EOD_JOB, run_date, ctx)


async def job_institutional_flow(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-postclose` — 個股三大法人 (Wide), cron **20:10**, its own 20:00 slot
    (deliberately not folded into the <=18:30 bundle)."""
    return await run_whole_market_job(INSTITUTIONAL_FLOW_JOB, run_date, ctx)


async def job_financials(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-monthly`/late-daily catch-all for financial-statement filings, cron **21:30**.
    A day with no filings legitimately returns zero rows — filings are irregular per-company,
    not a daily whole-market snapshot."""
    return await run_whole_market_job(FINANCIALS_JOB, run_date, ctx)


async def job_dividends(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-monthly`/late-daily catch-all for dividend-policy announcements, cron **21:30**.
    A day with no announcements legitimately returns zero rows — announcements are irregular
    per-company, not a daily whole-market snapshot."""
    return await run_whole_market_job(DIVIDENDS_JOB, run_date, ctx)


async def job_balance_sheet(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-monthly`/late-daily catch-all for balance-sheet filings, cron **21:30**. A day with
    no filings legitimately returns zero rows — filings are irregular per-company."""
    return await run_whole_market_job(BALANCE_SHEET_JOB, run_date, ctx)


async def job_cash_flow(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-monthly`/late-daily catch-all for cash-flow-statement filings, cron **21:30**. A
    day with no filings legitimately returns zero rows — filings are irregular per-company."""
    return await run_whole_market_job(CASH_FLOW_JOB, run_date, ctx)


async def job_month_revenue(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-monthly`/late-daily catch-all for monthly-revenue filings, cron **21:30**. A day
    with no filings legitimately returns zero rows — filings are irregular per-company."""
    return await run_whole_market_job(MONTH_REVENUE_JOB, run_date, ctx)


async def job_margin_short(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-postclose` — daily whole-market margin-purchase/short-sale balance
    snapshot, cron **17:40**."""
    return await run_whole_market_job(MARGIN_SHORT_JOB, run_date, ctx)


async def job_valuation_ratios(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-postclose` — daily whole-market PER/PBR/dividend-yield snapshot,
    cron **18:10**."""
    return await run_whole_market_job(VALUATION_RATIOS_JOB, run_date, ctx)


async def job_market_value(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-late` Sponsor group — daily whole-market market-capitalization snapshot,
    cron **23:40**."""
    return await run_whole_market_job(MARKET_VALUE_JOB, run_date, ctx)


async def job_market_value_weight(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-late` Sponsor group — paired with `job_market_value` above (same FinMind
    ~23:30 publish clock, same Sponsor tier): daily whole-market index-weight-per-symbol
    snapshot, cron **23:40**."""
    return await run_whole_market_job(MARKET_VALUE_WEIGHT_JOB, run_date, ctx)


async def job_active_etf_holdings(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-late` Sponsor group — 主動ETF daily constituent snapshot, cron **21:15**."""
    return await run_whole_market_job(ACTIVE_ETF_HOLDING_JOB, run_date, ctx)


async def job_active_etf_holding_changes(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-late` Sponsor group — 主動ETF daily buy/sell change, cron **21:15**."""
    return await run_whole_market_job(ACTIVE_ETF_HOLDING_CHANGE_JOB, run_date, ctx)


async def job_stock_info(run_date: date_, ctx: IngestContext) -> JobRun:
    """Reference-roster group — stock/ETF universe + industry classification, cron **06:00**
    (see `STOCK_INFO_JOB`'s own comment for the cadence rationale). Still calendar-gated like
    every other `run_whole_market_job` caller — a non-trading day is a legitimate no-op even for
    reference data, since there is no reason to spend a FinMind call on a day nothing else in
    the pipeline runs either."""
    return await run_whole_market_job(STOCK_INFO_JOB, run_date, ctx)
