"""Ingest job functions — idempotent, callable, testable units. **This phase builds the job
functions themselves, not a scheduler/cron daemon** (a later scheduler phase owns that) — each
function here does one dataset's whole-market-by-date pull for one trading day, so that later
phase can wire it behind a cron trigger with zero change to the function bodies.

Every job function enforces, in code (not just doc), the same six governance constraints:

1. **Purpose-bounded** — calls `FinMindAdapter.fetch_whole_market`, the one legitimate
   whole-market caller (`sources/finmind.py`), never a per-symbol fan-out.
2. **Cadence-bounded** — one call per dataset per trading day; no retry loop (a failed/
   rate-limited fetch journals `FAILED` and returns — the *next* scheduled run retries; never
   a tight retry loop).
3. **Calendar-bounded** — `_run_if_trading_day` gates every job on `TradingDayCalendar`
   before it fetches anything; a non-trading day short-circuits with zero network calls.
4. **Not user data / non-trading** — writes only the shared `ParquetStore`; never a
   `user_id`-scoped write; never emits a signal.
5. **Quota-fair** — whole-market-by-date is ~1 call/dataset/day; this module never issues a
   per-symbol call for market-wide EOD (the "per-symbol trap").
6. **Auditable + leader-elected** — `journal_run()` records every attempt; `LeaderElection.
   is_leader()` is checked before any write.
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from dataclasses import dataclass
from datetime import date as date_
from datetime import timedelta
from typing import cast

import structlog
from pydantic import BaseModel, ValidationError

from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run
from schemas.capabilities import (
    ActiveEtfHoldingChangeRow,
    ActiveEtfHoldingRow,
    DividendEvent,
    EODBar,
    EtfExpenseInfo,
    EtfNavRow,
    FinancialStatementLine,
    InstitutionalFlowRow,
    MarketValueRow,
    MarketValueWeightRow,
    MonthRevenueRow,
    ShareholdingDistributionRow,
    StockInfoRow,
)
from schemas.enums import Capability
from schemas.source import HealthStatus, SourceResult
from sources.sitca import fetch_whole_market_etf_fees
from sources.twse_etf_nav import fetch_whole_market_etf_nav

# Dataset-name constants for the two non-FinMind ETF-specific whole-market pulls in this module
# (`job_etf_nav`/`job_etf_expense_ratio`) — `analytics/scoring_batch.py` duplicates these same
# literal strings as its own local read-key constants rather than importing them (that module's
# own comment: a deliberate decoupling convention so the derived-compute layer never binds to
# this scheduler-facing module's wiring). Keep the two literal strings identical if either ever
# changes.
ETF_NAV_DATASET = "etf_nav"
ETF_EXPENSE_RATIO_DATASET = "etf_expense_ratio"

logger = structlog.get_logger(__name__)

# Shared across FINANCIALS/BALANCE_SHEET/CASH_FLOW below — all three statement families are
# filed on the same irregular, per-company statutory reporting cadence (no fixed daily publish
# clock), unlike the intraday EOD/institutional-flow datasets.
_IRREGULAR_PER_FILING_UPDATE_TIME = (
    "irregular per-filing (no fixed daily publish clock; statutory "
    "reporting-period cadence, not intraday)"
)


@dataclass(frozen=True)
class WholeMarketJobSpec:
    """One cron-job-group entry. `cron_hint` documents the intended fire time for a future
    scheduler to use — this phase does not schedule anything."""

    dataset: str  # FinMind dataset name, for the journal — human-identifiable, not a Capability
    capability: Capability
    finmind_update_time: str  # documented FinMind `Update:` time, TW time
    cron_hint: str  # update time + ~10min buffer, so the cron always fires after FinMind publishes
    # The pydantic model every normalized row must validate against before `write_partition` ever
    # sees it — the one write-time chokepoint `run_whole_market_job` enforces for every spec (see
    # `test_stock_info_drops_row_with_null_date_before_writing`'s own incident history: a `date:
    # null` upstream row previously round-tripped through Parquet as the literal string `"None"`,
    # which then failed validation on every subsequent READ instead of being dropped at write
    # time). A row failing this model is dropped, never written, never counted in `rows_written`.
    row_model: type[BaseModel]


# The two whole-market-by-date datasets this phase wires end-to-end (the
# `ingest-daily-postclose` group) — chosen as the representative EOD-append and
# institutional-flow-append cases; the remaining cadence-table datasets are enumerated as
# `STUBBED_DATASETS` below rather than half-implemented (see module docstring for rationale).
PRICE_EOD_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockPrice",
    capability=Capability.PRICE_EOD,
    finmind_update_time="Mon-Fri 17:30",
    cron_hint="17:40 Asia/Taipei",
    row_model=EODBar,
)

INSTITUTIONAL_FLOW_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockInstitutionalInvestorsBuySell",
    capability=Capability.INSTITUTIONAL_FLOW,
    finmind_update_time="Mon-Fri 20:00 (documented under the Wide dataset)",
    cron_hint="20:10 Asia/Taipei",
    row_model=InstitutionalFlowRow,
)

# `TaiwanStockFinancialStatements` documents the same omit-data_id whole-table request form as
# the two datasets above, but its own cadence is not a daily clock-time EOD publish — filings
# land on irregular, per-company statutory reporting dates, not a fixed post-close time. This
# job is still a once-a-day whole-market-by-date pull (so a day with no filings that day
# legitimately returns zero rows, same as any other empty trading day), scheduled well after
# the other post-close pulls rather than pinned to a documented same-day publish clock.
FINANCIALS_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockFinancialStatements",
    capability=Capability.FINANCIALS,
    finmind_update_time=_IRREGULAR_PER_FILING_UPDATE_TIME,
    cron_hint="21:30 Asia/Taipei (after the other post-close pulls; catches same-day filings)",
    row_model=FinancialStatementLine,
)

# `TaiwanStockDividend` likewise documents the omit-data_id whole-table form, and likewise has
# no fixed daily publish clock — dividend-policy rows land on board-resolution/announcement
# dates, not a market-close schedule. Same rationale as FINANCIALS_JOB above.
DIVIDENDS_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockDividend",
    capability=Capability.DIVIDENDS,
    finmind_update_time="irregular per-announcement (no fixed daily publish clock; "
    "board-resolution-driven, not intraday)",
    cron_hint="21:30 Asia/Taipei (after the other post-close pulls; catches same-day filings)",
    row_model=DividendEvent,
)

# `TaiwanStockMonthRevenue` — same irregular, per-company statutory cadence as the income/balance/
# cash-flow statements above (filed monthly, not on a fixed daily clock), and likewise already
# `whole_market_ok=True` in the FinMind source config. Ingested so `revenue_yoy` (this month's
# revenue vs the same month a year prior) can be computed from the derived store rather than a
# live per-symbol fetch on every `get_company_profile` call — the same "whole-market dataset,
# read back not fetched live" rationale `MARKET_VALUE_JOB` documents above.
MONTH_REVENUE_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockMonthRevenue",
    capability=Capability.MONTH_REVENUE,
    finmind_update_time=_IRREGULAR_PER_FILING_UPDATE_TIME,
    cron_hint="21:30 Asia/Taipei (with the other statement pulls; catches same-day filings)",
    row_model=MonthRevenueRow,
)

# The balance-sheet and cash-flow statement families share the same irregular per-filing cadence
# and the same documented omit-data_id whole-table request form as FINANCIALS above. They are
# ingested alongside the income statement because the derived multi-factor quality/value read
# needs line items from all three statement families (equity/liabilities/assets/cash from the
# balance sheet; operating cash flow and capex from the cash-flow statement) — the income
# statement alone cannot back ROE, debt/equity, ROIC, EV/EBITDA, or FCF yield.
BALANCE_SHEET_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockBalanceSheet",
    capability=Capability.BALANCE_SHEET,
    finmind_update_time=_IRREGULAR_PER_FILING_UPDATE_TIME,
    cron_hint="21:30 Asia/Taipei (with the other statement pulls; catches same-day filings)",
    row_model=FinancialStatementLine,
)

CASH_FLOW_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockCashFlowsStatement",
    capability=Capability.CASH_FLOW_STATEMENT,
    finmind_update_time=_IRREGULAR_PER_FILING_UPDATE_TIME,
    cron_hint="21:30 Asia/Taipei (with the other statement pulls; catches same-day filings)",
    row_model=FinancialStatementLine,
)

# `TaiwanStockMarketValue` — Sponsor-tier daily market-capitalization snapshot, documented with
# the same omit-data_id whole-table form and a Mon-Fri ~23:30 publish clock. Ingested because the
# derived value factor's EV/EBITDA and FCF-yield sub-metrics need market cap, and it is a
# whole-market dataset (never a per-symbol request-path fetch) — so it belongs in the derived
# store alongside the statements, read back the same way, not fetched live per symbol.
MARKET_VALUE_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockMarketValue",
    capability=Capability.MARKET_VALUE,
    finmind_update_time="Mon-Fri ~23:30 (Sponsor)",
    cron_hint="23:40 Asia/Taipei",
    row_model=MarketValueRow,
)

# `TaiwanStockMarketValueWeight` — the paired FinMind publish alongside `TaiwanStockMarketValue`
# above (same ~23:30 Sponsor-tier publish clock); feeds the derived value factor's index-weight
# context the same way MARKET_VALUE_JOB feeds market cap.
MARKET_VALUE_WEIGHT_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockMarketValueWeight",
    capability=Capability.MARKET_VALUE_WEIGHT,
    finmind_update_time="Mon-Fri ~23:30 (Sponsor)",
    cron_hint="23:40 Asia/Taipei",
    row_model=MarketValueWeightRow,
)

# Active-ETF holdings + holding-change — Sponsor tier, both documented with a whole-market-by-
# date (omit `data_id`) request form, published after the other Sponsor-tier late-daily pulls
# (分點/八大官股/產業鏈資金流). Both are ingested (not just the pre-diffed Change dataset) because
# the cross-ETF money-flow aggregation (`get_etf_money_flow`) needs the raw per-constituent
# `market_value`/`weight` snapshot (持有市值/檔數) that only `Holding` carries — `HoldingChange`
# alone (buy/sell share counts) cannot back that aggregation. Single-ETF tool calls
# (`get_active_etf_report`) still go through the per-symbol router path, not this whole-market
# store — this whole-market ingest exists for the cross-ETF aggregation case specifically.
ACTIVE_ETF_HOLDING_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockActiveETFHolding",
    capability=Capability.ACTIVE_ETF_HOLDING,
    finmind_update_time="21:00 (Sponsor)",
    cron_hint="21:15 Asia/Taipei",
    row_model=ActiveEtfHoldingRow,
)

ACTIVE_ETF_HOLDING_CHANGE_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockActiveETFHoldingChange",
    capability=Capability.ACTIVE_ETF_HOLDING_CHANGE,
    finmind_update_time="21:00 (Sponsor)",
    cron_hint="21:15 Asia/Taipei",
    row_model=ActiveEtfHoldingChangeRow,
)

# `TaiwanStockInfo` — the whole-roster reference table (stock universe: industry classification,
# security type). Daily cadence (not monthly like most reference tables) — same-day listings/
# reclassifications must not sit stale for up to a month, and `get_sector_heatmap`'s industry
# join against the same day's `price_eod` partition needs a fresh roster every trading day.
STOCK_INFO_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockInfo",
    capability=Capability.STOCK_INFO,
    finmind_update_time="once-daily, off-peak",
    cron_hint="06:00 Asia/Taipei",
    row_model=StockInfoRow,
)

# `TaiwanStockHoldingSharesPer` — Backer/Sponsor-tier weekly 股權持股分級表. Unlike every other
# `WholeMarketJobSpec` here, its own `job_shareholding_distribution` wrapper does NOT call
# `run_whole_market_job` directly with `run_date` — see that function's own docstring for why the
# exact weekly publish date must be resolved by backward search first. Still declared as a
# `WholeMarketJobSpec` (dataset/capability/row_model) so it participates in `ALL_WHOLE_MARKET_JOBS`
# and the daemon's completeness test, even though its own job function's control flow differs.
SHAREHOLDING_DISTRIBUTION_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockHoldingSharesPer",
    capability=Capability.SHAREHOLDING_DISTRIBUTION,
    finmind_update_time="weekly (published for each week's final trading day)",
    cron_hint="06:05 Asia/Taipei",
    row_model=ShareholdingDistributionRow,
)

# Every whole-market job spec this phase wires end-to-end, in the same order the daemon fires
# them — the canonical list a caller (the backfill CLI, tests asserting completeness) walks
# instead of hand-copying the specs above, so a newly added spec is picked up automatically.
ALL_WHOLE_MARKET_JOBS: tuple[WholeMarketJobSpec, ...] = (
    PRICE_EOD_JOB,
    INSTITUTIONAL_FLOW_JOB,
    FINANCIALS_JOB,
    DIVIDENDS_JOB,
    BALANCE_SHEET_JOB,
    CASH_FLOW_JOB,
    MONTH_REVENUE_JOB,
    MARKET_VALUE_JOB,
    MARKET_VALUE_WEIGHT_JOB,
    ACTIVE_ETF_HOLDING_JOB,
    ACTIVE_ETF_HOLDING_CHANGE_JOB,
    STOCK_INFO_JOB,
    SHAREHOLDING_DISTRIBUTION_JOB,
)

# The subset of `ALL_WHOLE_MARKET_JOBS` the backfill CLI's generic per-calendar-day sweep
# (`run_backfill_cli`) walks — every spec EXCEPT `SHAREHOLDING_DISTRIBUTION_JOB`, whose weekly
# publish-date-search control flow (`job_shareholding_distribution`'s own docstring) does not fit
# the generic "one date per day in range" sweep `run_preflight_if_enabled`/`backfill_range` walk;
# it is backfilled separately by `_backfill_shareholding_distribution`.
DAILY_WHOLE_MARKET_JOBS: tuple[WholeMarketJobSpec, ...] = tuple(
    spec for spec in ALL_WHOLE_MARKET_JOBS if spec is not SHAREHOLDING_DISTRIBUTION_JOB
)

# Documented but not implemented this phase — listed so a future scheduler-wiring phase has a
# checklist, and so "what's covered" is explicit rather than silently partial.
# Each needs either a new `_DatasetConfig.whole_market_ok=True` entry in `sources/finmind.py`
# (for FinMind whole-market datasets not yet modeled in `schemas.enums.Capability`) or a
# gov-OpenAPI same-day-authoritative adapter (處置/注意股) that doesn't exist yet.
# `chip_broker` (`TaiwanStockTradingDailyReport`) is deliberately NOT here as a to-do: its
# special endpoint only accepts a request keyed by *either* a stock id *or* a broker id, never
# neither — there is no documented omit-both whole-market form for the request mechanism this
# adapter actually uses (the separate bulk signed-URL download endpoint that does ignore both
# is a different fetch mechanism entirely, not something this adapter speaks). It stays
# per-symbol/per-broker, reachable only through the existing per-request tool path.
STUBBED_DATASETS: tuple[str, ...] = (
    "市場總法人/借券成交 (15:10)",
    "TaiwanStockTotalReturnIndex 報酬指數 (17:00)",
    "PER/PBR/殖利率, 漲跌停 (18:10)",
    "處置/注意股 + TaiwanOptionVix (18:00-18:10)",
    "TaiwanFuturesDaily/TaiwanOptionDaily + regular 期選法人 (20:10)",
    "ingest-daily-late: 融資融券/外資持股/信用總量/大盤維持率 (21:05)",
    "ingest-daily-late: 當沖 values TaiwanStockDayTrading (21:40)",
    "ingest-daily-late Sponsor: 分點/八大官股/產業鏈資金流 (chip_broker — no whole-market "
    "request form; per-symbol/per-broker only)",
    "ingest-reconcile (~08:30 next-morning re-pull)",
    "ingest-monthly: 景氣信號",
    "ingest-event: MOPS/news/PTT/rebalance/macro/IPO/yfinance",
)


def _fetch_error_status(result: SourceResult) -> RunStatus:
    if result.health.status in (HealthStatus.OK, HealthStatus.STALE):
        # STALE-with-no-exception still means "the call succeeded, empty body" — a legitimate
        # empty trading day is SUCCESS with rows=0, not an error.
        return RunStatus.SUCCESS
    return RunStatus.FAILED


def _validate_rows_against(
    rows: list[dict[str, object]], row_model: type[BaseModel], *, dataset: str
) -> list[dict[str, object]]:
    """Validate every normalized row against `row_model` and drop any that fail — the one
    write-time chokepoint that stands between a malformed upstream row and `write_partition`.

    A row failing validation here is silently dropped, never written, never counted in
    `rows_written` — logged once at `warning` so a systematic upstream shape change is still
    visible, without failing the whole day's ingest over one bad row. Confirmed real incident
    (`test_stock_info_drops_row_with_null_date_before_writing`): a `date: null` row previously
    round-tripped through Parquet as the literal string `"None"`, which then failed validation
    on every subsequent READ instead of being caught once, here, at write time."""
    valid: list[dict[str, object]] = []
    dropped = 0
    for row in rows:
        try:
            row_model.model_validate(row)
        except ValidationError:
            dropped += 1
            continue
        valid.append(row)
    if dropped:
        logger.warning(
            "ingest_row_validation_dropped",
            dataset=dataset,
            dropped=dropped,
            kept=len(valid),
        )
    return valid


def _run_if_trading_day(
    calendar: TradingDayCalendar, run_date: date_, dataset: str
) -> JobRun | None:
    """Calendar gate: return a SKIPPED journal entry and do nothing else if `run_date` is not
    a trading day — no fetch is even attempted."""
    if calendar.is_trading_day(run_date):
        return None
    return journal_run(
        dataset,
        run_date,
        rows_written=0,
        status=RunStatus.SKIPPED,
        detail="non-trading day (calendar-gated)",
    )


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
    result = await ctx.finmind.fetch_whole_market(
        spec.capability, tls_policy=ctx.tls_policy, start_date=date_str
    )
    if _fetch_error_status(result) is RunStatus.FAILED:
        # Never retry-loop tightly — journal the failure and let the next scheduled run (or
        # an explicit gap-fill) retry.
        logger.warning(
            "ingest_whole_market_fetch_failed",
            dataset=spec.dataset,
            run_date=date_str,
            detail=result.health.detail,
        )
        return journal_run(
            spec.dataset,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=result.health.detail,
        )

    row_key = next(iter(result.payload), None)
    rows_obj: object = result.payload.get(row_key, []) if row_key else []
    rows: list[dict[str, object]] = (
        cast("list[dict[str, object]]", rows_obj) if isinstance(rows_obj, list) else []
    )
    rows = _validate_rows_against(rows, spec.row_model, dataset=spec.dataset)
    rows_written = ctx.store.write_partition(spec.dataset, ctx.market.value, run_date, rows)
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
        detail=result.health.detail,
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


async def job_market_value(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-late` Sponsor group — daily whole-market market-capitalization snapshot,
    cron **23:40**."""
    return await run_whole_market_job(MARKET_VALUE_JOB, run_date, ctx)


async def job_active_etf_holdings(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-late` Sponsor group — 主動ETF daily constituent snapshot, cron **21:15**."""
    return await run_whole_market_job(ACTIVE_ETF_HOLDING_JOB, run_date, ctx)


async def job_active_etf_holding_changes(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-late` Sponsor group — 主動ETF daily buy/sell change, cron **21:15**."""
    return await run_whole_market_job(ACTIVE_ETF_HOLDING_CHANGE_JOB, run_date, ctx)


async def job_market_value_weight(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-late` Sponsor group — market-cap-weight ranking, cron **23:40**, paired
    with `job_market_value` at the same FinMind publish clock."""
    return await run_whole_market_job(MARKET_VALUE_WEIGHT_JOB, run_date, ctx)


async def job_stock_info(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily-early` reference roster — daily (not monthly), cron **06:00**, so same-day
    listings/reclassifications never sit stale for up to a month (see `STOCK_INFO_JOB`'s own
    comment) and `get_sector_heatmap`'s industry join against the same day's `price_eod`
    partition always has a fresh roster."""
    return await run_whole_market_job(STOCK_INFO_JOB, run_date, ctx)


# How many calendar days to search backward from the caller's `run_date` for the real weekly
# publish date, in `job_shareholding_distribution` below — a full week plus a couple of days'
# margin, so a caller passing any day of the following week (the daemon always calls with
# "today", not necessarily the Friday the dataset actually published on) still finds the prior
# week's real publish date within the window.
WEEKLY_PUBLISH_SEARCH_DAYS = 7


async def job_shareholding_distribution(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-weekly` Sponsor group — 股權持股分級表 (`TaiwanStockHoldingSharesPer`), cron
    **06:05**. NOT trading-day-gated (same reasoning as the monthly reference-data jobs the
    daemon's own `Cadence.WEEKLY` docstring cites) — this dataset's own publish cadence, not the
    daily trading calendar, governs when a fresh row exists.

    Confirmed live: FinMind's `start_date` for this dataset must be the EXACT date the report
    was published (a Friday) — any other day in that week returns zero rows, unlike every other
    whole-market dataset in this module where `start_date` is just a lookback anchor. The
    scheduler always calls this job with "today" (whatever weekday the cron fires on), so this
    function itself searches backward from `run_date` — trying `run_date`, then `run_date - 1
    day`, … down to `run_date - WEEKLY_PUBLISH_SEARCH_DAYS` — for the first candidate date that
    actually returns rows, and writes the partition under THAT resolved date, not `run_date`.
    A caller that wants "this week's data" must read back by the dataset's own resolved
    publish-date partitions (`ctx.store.list_partition_dates`), not assume a fixed weekday.

    If no candidate in the search window returns rows, this is SKIPPED, not FAILED — a genuine
    gap (e.g. the daemon was down for the whole window) is reported the same way a non-trading
    day is elsewhere in this module, and the next scheduled run's own backward search naturally
    retries the whole window again."""
    spec = SHAREHOLDING_DISTRIBUTION_JOB
    if not ctx.leader.is_leader():
        return journal_run(
            spec.dataset,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    for offset in range(WEEKLY_PUBLISH_SEARCH_DAYS + 1):
        candidate = run_date - timedelta(days=offset)
        result = await ctx.finmind.fetch_whole_market(
            spec.capability, tls_policy=ctx.tls_policy, start_date=candidate.isoformat()
        )
        if _fetch_error_status(result) is RunStatus.FAILED:
            logger.warning(
                "ingest_whole_market_fetch_failed",
                dataset=spec.dataset,
                run_date=candidate.isoformat(),
                detail=result.health.detail,
            )
            continue
        rows_obj: object = result.payload.get("rows", [])
        rows: list[dict[str, object]] = (
            cast("list[dict[str, object]]", rows_obj) if isinstance(rows_obj, list) else []
        )
        if not rows:
            continue

        rows = _validate_rows_against(rows, spec.row_model, dataset=spec.dataset)
        rows_written = ctx.store.write_partition(spec.dataset, ctx.market.value, candidate, rows)
        await ctx.data_update_publisher.publish_data_updated(
            capability=spec.capability,
            market=ctx.market,
            as_of=candidate.isoformat(),
            scope="whole-market",
        )
        return journal_run(
            spec.dataset,
            candidate,
            rows_written=rows_written,
            status=RunStatus.SUCCESS,
            detail=result.health.detail,
        )

    return journal_run(
        spec.dataset,
        run_date,
        rows_written=0,
        status=RunStatus.SKIPPED,
        detail=(
            f"no publish found within {WEEKLY_PUBLISH_SEARCH_DAYS}-day backward search window"
        ),
    )


async def job_etf_nav(run_date: date_, ctx: IngestContext) -> JobRun:
    """ETF premium/discount-to-NAV daily snapshot, cron **21:20** — TWSE MIS's `all_etf.txt`
    (`sources/twse_etf_nav.py`), not FinMind, so this job calls its own dedicated whole-market
    fetch function directly rather than `run_whole_market_job`'s FinMind-specific path. Still
    the same calendar-gate / leader-gate / validate-then-write / FAILED-on-bad-health shape
    every other job in this module follows."""
    dataset = ETF_NAV_DATASET
    skipped = _run_if_trading_day(ctx.calendar, run_date, dataset)
    if skipped is not None:
        return skipped

    if not ctx.leader.is_leader():
        return journal_run(
            dataset,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    result = await fetch_whole_market_etf_nav(tls_policy=ctx.tls_policy)
    if _fetch_error_status(result) is RunStatus.FAILED:
        logger.warning(
            "ingest_whole_market_fetch_failed",
            dataset=dataset,
            run_date=run_date.isoformat(),
            detail=result.health.detail,
        )
        return journal_run(
            dataset,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=result.health.detail,
        )

    rows_obj: object = result.payload.get("rows", [])
    rows: list[dict[str, object]] = (
        cast("list[dict[str, object]]", rows_obj) if isinstance(rows_obj, list) else []
    )
    rows = _validate_rows_against(rows, EtfNavRow, dataset=dataset)
    rows_written = ctx.store.write_partition(dataset, ctx.market.value, run_date, rows)
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.ETF_NAV,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    return journal_run(
        dataset,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=result.health.detail,
    )


async def job_etf_expense_ratio(run_date: date_, ctx: IngestContext) -> JobRun:
    """ETF/fund internal-fee (內扣費用) whole-market snapshot, cron **21:25**, monthly cadence —
    SITCA's own fee report (`sources/sitca.py`) only refreshes monthly, so a fresh call more
    often than that would just re-read the same disclosed month. Not FinMind, and not
    trading-day-gated (same reasoning as the daemon's other `Cadence.MONTHLY` reference-data
    jobs) — only the leader gate applies."""
    dataset = ETF_EXPENSE_RATIO_DATASET
    if not ctx.leader.is_leader():
        return journal_run(
            dataset,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    result = await fetch_whole_market_etf_fees(tls_policy=ctx.tls_policy)
    if _fetch_error_status(result) is RunStatus.FAILED:
        logger.warning(
            "ingest_whole_market_fetch_failed",
            dataset=dataset,
            run_date=run_date.isoformat(),
            detail=result.health.detail,
        )
        return journal_run(
            dataset,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=result.health.detail,
        )

    rows_obj: object = result.payload.get("rows", [])
    rows: list[dict[str, object]] = (
        cast("list[dict[str, object]]", rows_obj) if isinstance(rows_obj, list) else []
    )
    rows = _validate_rows_against(rows, EtfExpenseInfo, dataset=dataset)
    rows_written = ctx.store.write_partition(dataset, ctx.market.value, run_date, rows)
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.ETF_EXPENSE_RATIO,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    return journal_run(
        dataset,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=result.health.detail,
    )


__all__ = [
    "ACTIVE_ETF_HOLDING_CHANGE_JOB",
    "ACTIVE_ETF_HOLDING_JOB",
    "ALL_WHOLE_MARKET_JOBS",
    "BALANCE_SHEET_JOB",
    "CASH_FLOW_JOB",
    "DAILY_WHOLE_MARKET_JOBS",
    "DIVIDENDS_JOB",
    "ETF_EXPENSE_RATIO_DATASET",
    "ETF_NAV_DATASET",
    "FINANCIALS_JOB",
    "INSTITUTIONAL_FLOW_JOB",
    "MARKET_VALUE_JOB",
    "MARKET_VALUE_WEIGHT_JOB",
    "MONTH_REVENUE_JOB",
    "PRICE_EOD_JOB",
    "SHAREHOLDING_DISTRIBUTION_JOB",
    "STOCK_INFO_JOB",
    "STUBBED_DATASETS",
    "WEEKLY_PUBLISH_SEARCH_DAYS",
    "WholeMarketJobSpec",
    "job_active_etf_holding_changes",
    "job_active_etf_holdings",
    "job_balance_sheet",
    "job_cash_flow",
    "job_dividends",
    "job_etf_expense_ratio",
    "job_etf_nav",
    "job_financials",
    "job_institutional_flow",
    "job_market_value",
    "job_market_value_weight",
    "job_month_revenue",
    "job_price_eod",
    "job_shareholding_distribution",
    "job_stock_info",
    "run_whole_market_job",
]
