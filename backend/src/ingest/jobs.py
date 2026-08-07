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
from typing import cast

import structlog

from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run
from schemas.enums import Capability
from schemas.source import HealthStatus, SourceResult

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class WholeMarketJobSpec:
    """One cron-job-group entry. `cron_hint` documents the intended fire time for a future
    scheduler to use — this phase does not schedule anything."""

    dataset: str  # FinMind dataset name, for the journal — human-identifiable, not a Capability
    capability: Capability
    finmind_update_time: str  # documented FinMind `Update:` time, TW time
    cron_hint: str  # update time + ~10min buffer, so the cron always fires after FinMind publishes


# The two whole-market-by-date datasets this phase wires end-to-end (the
# `ingest-daily-postclose` group) — chosen as the representative EOD-append and
# institutional-flow-append cases; the remaining cadence-table datasets are enumerated as
# `STUBBED_DATASETS` below rather than half-implemented (see module docstring for rationale).
PRICE_EOD_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockPrice",
    capability=Capability.PRICE_EOD,
    finmind_update_time="Mon-Fri 17:30",
    cron_hint="17:40 Asia/Taipei",
)

INSTITUTIONAL_FLOW_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockInstitutionalInvestorsBuySell",
    capability=Capability.INSTITUTIONAL_FLOW,
    finmind_update_time="Mon-Fri 20:00 (documented under the Wide dataset)",
    cron_hint="20:10 Asia/Taipei",
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
    finmind_update_time="irregular per-filing (no fixed daily publish clock; statutory "
    "reporting-period cadence, not intraday)",
    cron_hint="21:30 Asia/Taipei (after the other post-close pulls; catches same-day filings)",
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
    finmind_update_time="irregular per-filing (no fixed daily publish clock; statutory "
    "reporting-period cadence, not intraday)",
    cron_hint="21:30 Asia/Taipei (with the other statement pulls; catches same-day filings)",
)

CASH_FLOW_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockCashFlowsStatement",
    capability=Capability.CASH_FLOW_STATEMENT,
    finmind_update_time="irregular per-filing (no fixed daily publish clock; statutory "
    "reporting-period cadence, not intraday)",
    cron_hint="21:30 Asia/Taipei (with the other statement pulls; catches same-day filings)",
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
)

ACTIVE_ETF_HOLDING_CHANGE_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockActiveETFHoldingChange",
    capability=Capability.ACTIVE_ETF_HOLDING_CHANGE,
    finmind_update_time="21:00 (Sponsor)",
    cron_hint="21:15 Asia/Taipei",
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
    MARKET_VALUE_JOB,
    ACTIVE_ETF_HOLDING_JOB,
    ACTIVE_ETF_HOLDING_CHANGE_JOB,
)

# Documented but not implemented this phase — listed so a future scheduler-wiring phase has a
# checklist, and so "what's covered" is explicit rather than silently partial.
# Each needs either a new `_DatasetConfig.whole_market_ok=True` entry in `sources/finmind.py`
# (for FinMind whole-market datasets not yet modeled in `schemas.enums.Capability`) or a
# gov-OpenAPI same-day-authoritative adapter (處置/注意股, ETF NAV) that doesn't exist yet.
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
    "ETF NAV/IOPV/折溢價 (etf_pcf scrape, 15:30+20:00)",
    "ingest-daily-late: 融資融券/外資持股/信用總量/大盤維持率 (21:05)",
    "ingest-daily-late: 當沖 values TaiwanStockDayTrading (21:40)",
    "ingest-daily-late Sponsor: 分點/八大官股/產業鏈資金流 (chip_broker — no whole-market "
    "request form; per-symbol/per-broker only)",
    "ingest-daily-late: TaiwanStockMarketValue+Weight (23:40)",
    "ingest-reconcile (~08:30 next-morning re-pull)",
    "ingest-weekly: TaiwanStockHoldingSharesPer (Sun 06:00 + Mon reconcile)",
    "ingest-monthly: 月營收/景氣信號",
    "ingest-event: MOPS/news/PTT/rebalance/macro/IPO/yfinance",
)


def _fetch_error_status(result: SourceResult) -> RunStatus:
    if result.health.status in (HealthStatus.OK, HealthStatus.STALE):
        # STALE-with-no-exception still means "the call succeeded, empty body" — a legitimate
        # empty trading day is SUCCESS with rows=0, not an error.
        return RunStatus.SUCCESS
    return RunStatus.FAILED


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


__all__ = [
    "ACTIVE_ETF_HOLDING_CHANGE_JOB",
    "ACTIVE_ETF_HOLDING_JOB",
    "ALL_WHOLE_MARKET_JOBS",
    "BALANCE_SHEET_JOB",
    "CASH_FLOW_JOB",
    "DIVIDENDS_JOB",
    "FINANCIALS_JOB",
    "INSTITUTIONAL_FLOW_JOB",
    "MARKET_VALUE_JOB",
    "PRICE_EOD_JOB",
    "STUBBED_DATASETS",
    "WholeMarketJobSpec",
    "job_active_etf_holding_changes",
    "job_active_etf_holdings",
    "job_balance_sheet",
    "job_cash_flow",
    "job_dividends",
    "job_financials",
    "job_institutional_flow",
    "job_market_value",
    "job_price_eod",
    "run_whole_market_job",
]
