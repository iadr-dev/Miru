"""ETF NAV, expense-ratio and AUM jobs."""

from __future__ import annotations

import asyncio
from datetime import date as date_
from typing import cast

import structlog

from ingest.context import IngestContext
from ingest.jobs.whole_market import fetch_error_status as _fetch_error_status
from ingest.jobs.whole_market import validate_rows_against as _validate_rows_against
from ingest.journal import JobRun, RunStatus, journal_run
from schemas.capabilities import (
    EtfAumRow,
    EtfExpenseInfo,
    EtfNavRow,
)
from schemas.enums import Capability
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.sitca import MONTH_NOT_PUBLISHED_KEY, fetch_whole_market_etf_fees
from sources.twse_etf_nav import fetch_whole_market_etf_nav

_NOT_LEADER_DETAIL = "not the leader — write skipped"

_NON_TRADING_DETAIL = "non-trading day (calendar-gated)"

logger = structlog.get_logger(__name__)


ETF_NAV_DATASET = "etf_nav"


async def job_etf_nav(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily` — ETF premium/discount-to-NAV snapshot (`sources/twse_etf_nav.py`), cron
    **daily, off-peak** (see `ingest/daemon.py`'s `SCHEDULED_JOBS`).

    Not FinMind-sourced (unlike every other job in this module) — `fetch_whole_market_etf_nav`
    is `sources/twse_etf_nav.py`'s own dedicated whole-market fetch function, the same
    ingest-layer whole-market carve-out `FinMindAdapter.fetch_whole_market` provides for FinMind
    datasets, just for a different upstream (TWSE MIS's free `all_etf.txt`).

    Calendar-gated exactly like every other daily job: a non-trading day is a journaled skip,
    no network call. Uses the extrapolating check for the same reason `_run_if_trading_day`
    does — see its own docstring."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            ETF_NAV_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=_NON_TRADING_DETAIL,
        )
    if not ctx.leader.is_leader():
        return journal_run(
            ETF_NAV_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=_NOT_LEADER_DETAIL,
        )

    result = await fetch_whole_market_etf_nav(tls_policy=ctx.tls_policy)
    if _fetch_error_status(result) is RunStatus.FAILED:
        return journal_run(
            ETF_NAV_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=result.health.detail,
        )

    raw_rows_obj = result.payload.get("rows", [])
    raw_rows: list[dict[str, object]] = (
        cast("list[dict[str, object]]", raw_rows_obj) if isinstance(raw_rows_obj, list) else []
    )
    rows = _validate_rows_against(EtfNavRow, ETF_NAV_DATASET, raw_rows, run_date)
    rows_written = ctx.store.write_partition(ETF_NAV_DATASET, ctx.market.value, run_date, rows)
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.ETF_NAV,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    return journal_run(
        ETF_NAV_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=result.health.detail,
    )


ETF_EXPENSE_RATIO_DATASET = "etf_expense_ratio"


async def job_etf_expense_ratio(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-monthly` — ETF/fund internal-fee (內扣費用) whole-market snapshot
    (`sources/sitca.py`), cron **monthly, off-peak** (see `ingest/daemon.py`'s `SCHEDULED_JOBS`).

    Not FinMind-sourced — `fetch_whole_market_etf_fees` is `sources/sitca.py`'s own dedicated
    whole-market fetch function, the same ingest-layer whole-market carve-out `job_etf_nav` uses
    for its own non-FinMind upstream (SITCA's monthly IN2105 fee report, rather than a
    per-symbol live fetch on every `get_etf_expense_ratio` tool call).

    Unlike `job_etf_nav`/`job_etf_aum`/the four MOPS+TWSE/TPEX jobs it shares
    `_run_non_finmind_forward_only_jobs_for_today`'s treatment with, SITCA's report genuinely
    supports a historical query — its postback form's month dropdown serves real per-fund rows
    for any past month, confirmed live, not just the latest. This job passes `run_date`'s
    `YYYYMM` straight through to `fetch_whole_market_etf_fees`, so a backfill caller walking
    multiple months writes
    each one under its own real disclosure month rather than always overwriting with "latest.\"

    Calendar-gated exactly like every other daily-cadence job in this module — a non-trading
    day is a journaled skip, no network call. SITCA's own report only refreshes monthly, but the
    scheduler's monthly cadence gate (not this function) is what limits actual fetch frequency;
    this function itself is a plain calendar+leader-gated whole-market pull, same shape as
    `job_etf_nav`."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            ETF_EXPENSE_RATIO_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=_NON_TRADING_DETAIL,
        )
    if not ctx.leader.is_leader():
        return journal_run(
            ETF_EXPENSE_RATIO_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=_NOT_LEADER_DETAIL,
        )

    result = await fetch_whole_market_etf_fees(
        tls_policy=ctx.tls_policy, year_month=run_date.strftime("%Y%m")
    )
    if result.payload.get(MONTH_NOT_PUBLISHED_KEY) is True:
        return journal_run(
            ETF_EXPENSE_RATIO_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=result.health.detail,
        )
    if _fetch_error_status(result) is RunStatus.FAILED:
        return journal_run(
            ETF_EXPENSE_RATIO_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=result.health.detail,
        )

    raw_rows_obj = result.payload.get("rows", [])
    raw_rows: list[dict[str, object]] = (
        cast("list[dict[str, object]]", raw_rows_obj) if isinstance(raw_rows_obj, list) else []
    )
    rows = _validate_rows_against(EtfExpenseInfo, ETF_EXPENSE_RATIO_DATASET, raw_rows, run_date)
    rows_written = ctx.store.write_partition(
        ETF_EXPENSE_RATIO_DATASET, ctx.market.value, run_date, rows
    )
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.ETF_EXPENSE_RATIO,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    return journal_run(
        ETF_EXPENSE_RATIO_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=result.health.detail,
    )


ETF_AUM_DATASET = "etf_aum"


# yfinance has no whole-market ETF endpoint (same gap `ingest/us_jobs.py::job_us_fundamentals`
# documents for US fundamentals) — this job's per-symbol loop is the accepted fan-out exception,
# paced identically to that job's own throttle to avoid the same rate-limiting failure mode
# (455 "Too Many Requests" in under 4 minutes, confirmed empirically there with no delay).
_YFINANCE_THROTTLE_SECONDS = 0.5


def _etf_nav_rows_as_of(ctx: IngestContext, run_date: date_) -> list[dict[str, object]]:
    """The newest `etf_nav` partition dated on or before `run_date` (TWSE's own whole-market ETF
    snapshot, `job_etf_nav`) — never a later one, so re-running a past date derives from that
    day's own units and NAV rather than today's. A live nightly run has `run_date` at or after
    every partition, so it still reads the latest. Empty (never a crash) if none exists yet."""
    dates = [
        d
        for d in ctx.store.list_partition_dates(ETF_NAV_DATASET, ctx.market.value)
        if d <= run_date
    ]
    if not dates:
        return []
    return ctx.store.read_partition(ETF_NAV_DATASET, ctx.market.value, max(dates))


def _etf_nav_symbols(nav_rows: list[dict[str, object]]) -> tuple[str, ...]:
    """The ETF universe to loop AUM over — the symbols in that `etf_nav` roster rather than a
    hardcoded list, mirroring `ingest/us_universe.py::latest_us_symbols`'s "read a roster ingest
    already wrote" pattern."""
    field_names = set(EtfNavRow.model_fields)
    symbols = {
        str(row["symbol"]) for row in nav_rows if "symbol" in {k for k in row if k in field_names}
    }
    return tuple(sorted(symbols))


def _derived_etf_aum_rows(
    ctx: IngestContext, nav_rows: list[dict[str, object]], run_date: date_
) -> list[dict[str, object]]:
    """AUM = `units_outstanding x prior_day_nav`, from the `etf_nav` rows (TWSE MIS's bulk
    `all_etf.txt`, `job_etf_nav`) — no extra request, and it covers every
    listed ETF including the bond ETFs Yahoo has no `totalAssets` for. `prior_day_nav` is the
    issuer's settled unit NAV and is preferred. TWSE sometimes omits it for a handful of
    overseas-bond ETFs around a holiday (observed 2026-09-25/28 for 10 ETFs) while still
    publishing `estimated_nav`; that estimate sits a median ~0.3% from the settled NAV
    (p90 ~1.3%, measured across the roster), so it is used in that case rather than leaving the
    ETF with no AUM at all. A row with no positive units, or no positive NAV of either kind, is
    skipped (left for the Yahoo fallback), never written as a guess."""
    rows: list[dict[str, object]] = []
    for raw in nav_rows:
        symbol = raw.get("symbol")
        units = _positive_number(raw.get("units_outstanding"))
        nav = _positive_number(raw.get("prior_day_nav")) or _positive_number(
            raw.get("estimated_nav")
        )
        if not isinstance(symbol, str) or units is None or nav is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "market": ctx.market.value,
                "as_of": run_date.isoformat(),
                "aum": units * nav,
            }
        )
    return rows


def _positive_number(value: object) -> float | None:
    """`value` as a float if it is a real, positive number; else `None` (bool excluded)."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return float(value)


async def _fetch_one_etf_aum(ctx: IngestContext, symbol: str) -> dict[str, object] | None:
    """One symbol's AUM fetch, classified into a row-to-keep or `None` (a real failure the
    caller should count) — mirrors `ingest/us_jobs.py::_fetch_one_fundamental`'s classification:
    a raised exception and a degraded (never-raised) `UNREACHABLE`/`UNAVAILABLE_TIER` result
    both count as failures, never silently reported as a zero-row SUCCESS."""
    sym = Symbol(id=symbol, market=ctx.market)
    try:
        result = await ctx.yfinance.fetch(  # type: ignore[union-attr]
            Capability.ETF_AUM, sym, tls_policy=ctx.tls_policy
        )
    except Exception as exc:  # one symbol's failure never aborts the batch
        logger.warning("etf_aum_fetch_failed", symbol=symbol, error=str(exc))
        return None
    if result.health.status in (HealthStatus.UNREACHABLE, HealthStatus.UNAVAILABLE_TIER):
        logger.warning(
            "etf_aum_fetch_degraded",
            symbol=symbol,
            status=result.health.status.value,
            detail=result.health.detail,
        )
        return None
    row = result.payload.get("aum")
    return cast("dict[str, object]", row) if isinstance(row, dict) else None


async def job_etf_aum(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-late` — ETF total-net-assets (資產規模) whole-market snapshot.

    Primary source is derived locally from the ingested `etf_nav` partition
    (`_derived_etf_aum_rows`: TWSE's units outstanding x prior-day NAV) — one bulk upstream,
    already fetched, covering every listed ETF. Yahoo (`YFinanceAdapter`, per-symbol, paced —
    see module-level `_YFINANCE_THROTTLE_SECONDS`'s docstring) is only a fallback for an ETF
    whose NAV row lacks either input; it used to be the sole source, and its throttling plus no
    `totalAssets` for bond ETFs left ~1/3 of the roster without a row.

    Symbol universe comes from `_etf_nav_symbols` (the `etf_nav` roster as of `run_date`),
    never a hardcoded list — an empty universe (that job hasn't run yet) is a graceful skip.
    One fallback symbol's failure never aborts the run: it's counted, journaled as `PARTIAL` if
    any symbol failed but some rows still landed, `FAILED` only if none did."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            ETF_AUM_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=_NON_TRADING_DETAIL,
        )
    if not ctx.leader.is_leader():
        return journal_run(
            ETF_AUM_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=_NOT_LEADER_DETAIL,
        )

    nav_rows = _etf_nav_rows_as_of(ctx, run_date)
    symbols = _etf_nav_symbols(nav_rows)
    if not symbols:
        return journal_run(
            ETF_AUM_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="no etf_nav roster yet — run job_etf_nav first",
        )

    raw_rows = _derived_etf_aum_rows(ctx, nav_rows, run_date)
    derived_count = len(raw_rows)
    derived_symbols = {str(row["symbol"]) for row in raw_rows}
    fallback_symbols = [symbol for symbol in symbols if symbol not in derived_symbols]
    failures = 0
    yahoo_count = 0
    for index, symbol in enumerate(fallback_symbols):
        if ctx.yfinance is None:
            failures += len(fallback_symbols)
            break
        if index > 0:
            # Paced, not concurrent — see `_YFINANCE_THROTTLE_SECONDS`'s own docstring.
            await asyncio.sleep(_YFINANCE_THROTTLE_SECONDS)
        row = await _fetch_one_etf_aum(ctx, symbol)
        if row is None:
            failures += 1
            continue
        raw_rows.append(row)
        yahoo_count += 1

    rows = _validate_rows_against(EtfAumRow, ETF_AUM_DATASET, raw_rows, run_date)
    rows_written = ctx.store.write_partition(ETF_AUM_DATASET, ctx.market.value, run_date, rows)
    if rows_written:
        await ctx.data_update_publisher.publish_data_updated(
            capability=Capability.ETF_AUM,
            market=ctx.market,
            as_of=run_date.isoformat(),
            scope="whole-market",
        )
    status = RunStatus.SUCCESS
    if failures:
        status = RunStatus.FAILED if rows_written == 0 else RunStatus.PARTIAL
    return journal_run(
        ETF_AUM_DATASET,
        run_date,
        rows_written=rows_written,
        status=status,
        detail=(
            f"{derived_count} derived from TWSE units x NAV, {yahoo_count} from Yahoo fallback"
            + (f", {failures} symbol fetch(es) failed" if failures else "")
        ),
    )
