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

import asyncio
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
    AttentionStockRow,
    DispositionEventRow,
    DividendEvent,
    EODBar,
    EtfAumRow,
    EtfExpenseInfo,
    EtfNavRow,
    FinancialStatementLine,
    InsiderTransferDeclarationRow,
    InstitutionalFlowRow,
    MarginShortRow,
    MarketValueRow,
    MarketValueWeightRow,
    MaterialAnnouncementRow,
    MonthRevenueRow,
    ShareholdingDistributionRow,
    StockInfoRow,
    ValuationRatiosRow,
)
from schemas.enums import Capability
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceResult
from sources.mops import (
    fetch_whole_market_insider_transfer_declarations,
    fetch_whole_market_material_announcements,
)
from sources.sitca import MONTH_NOT_PUBLISHED_KEY, fetch_whole_market_etf_fees
from sources.tpex_openapi import (
    fetch_whole_market_attention_stocks_tpex,
    fetch_whole_market_disposition_events_tpex,
)
from sources.twse_etf_nav import fetch_whole_market_etf_nav
from sources.twse_openapi import (
    fetch_whole_market_attention_stocks,
    fetch_whole_market_disposition_events,
)

# `TaiwanStockInfo` is a reference roster (stock/ETF universe + `industry_category` per symbol),
# `TimingClass.REFERENCE` (`sources/finmind/technical.py`'s own `_DatasetConfig` caveat: "no
# `data_id` is ever meaningful — the full list always comes back"), not a clock-time daily EOD
# publish. Distinct wording from `_IRREGULAR_PER_FILING_UPDATE_TIME` above (that's per-filing
# statutory cadence; this is a slow-changing universe listing) so a reader doesn't conflate the
# two different "no fixed clock" reasons.
_REFERENCE_ROSTER_UPDATE_TIME = (
    "reference roster (stock/warrant universe); no fixed daily publish clock — the roster "
    "changes only on new listings/delistings/industry-reclassification, not a daily refresh"
)

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
    scheduler to use — this phase does not schedule anything.

    `row_model` is the canonical `schemas.capabilities` row shape for this dataset — the same
    model any reader (`_rows_from_store` in `tools/market_tools.py`, `fetch_rows` in
    `tools/_rows.py`) validates a read-back row against. `run_whole_market_job` validates every
    row against it BEFORE writing, so a malformed upstream row (a `None`/missing field FinMind's
    raw JSON happens to carry for one row of one day) is caught and dropped at the one ingest
    chokepoint, never persisted to Parquet to crash a reader days or months later. This is the
    fix for a real incident: an unvalidated `TaiwanStockInfo` row with `date: null` was written
    as-is, and a `pandas.DataFrame` with a mixed None/str `date` column serialized that `None` as
    the literal text `"None"` on its way through Parquet — which then failed `StockInfoRow`
    validation on every subsequent read. Validating here, once, before any write, is the correct
    fix — not a defensive read-side workaround, since the derived store can be rebuilt from
    scratch at any time and must never receive bad rows again."""

    dataset: str  # FinMind dataset name, for the journal — human-identifiable, not a Capability
    capability: Capability
    finmind_update_time: str  # documented FinMind `Update:` time, TW time
    cron_hint: str  # update time + ~10min buffer, so the cron always fires after FinMind publishes
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

# `TaiwanStockMarginPurchaseShortSale` — daily whole-market margin-purchase/short-sale balance
# snapshot, already `whole_market_ok=True` in `sources/finmind/chip_institutional.py`'s own
# `_DatasetConfig`. Ingested so a 融資增減 (day-over-day margin-balance-change) ranking can be
# computed from the derived store — `margin_purchase_today_balance`/`margin_purchase_yesterday_
# balance` are both carried in the same row, so this needs no extra history, just today's
# whole-market partition read back.
MARGIN_SHORT_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockMarginPurchaseShortSale",
    capability=Capability.MARGIN_SHORT,
    finmind_update_time="Mon-Fri ~17:30 (documented alongside the other daily EOD chip datasets)",
    cron_hint="17:40 Asia/Taipei",
    row_model=MarginShortRow,
)

# `TaiwanStockPER` — daily whole-market PER/PBR/dividend-yield snapshot. Not `whole_market_ok`
# by default in `sources/finmind/technical.py`'s own `_DatasetConfig` (that flag was added
# specifically to support this job); ingested so a 股息殖利率 (dividend-yield) ranking can read
# the latest whole-market partition instead of a per-symbol live fetch per candidate.
VALUATION_RATIOS_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockPER",
    capability=Capability.VALUATION_RATIOS,
    finmind_update_time="Mon-Fri ~18:00 (post-close, not intraday)",
    cron_hint="18:10 Asia/Taipei",
    row_model=ValuationRatiosRow,
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
# above: same Sponsor tier, same omit-data_id whole-table form, same Mon-Fri ~23:30 publish
# clock (both halves of the same daily market-cap snapshot land together). `MarketValue` carries
# raw market cap; `MarketValueWeight` carries each symbol's weight-of-index share, which is the
# input `get_index_contribution` needs to estimate a symbol's contribution to an index move
# (weight x price change) — market cap alone cannot back that without also knowing the index's
# total float-adjusted base, which this dataset does not carry either (see the tool's
# `contribution_caveat` field for the resulting approximation this implies).
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

# `TaiwanStockInfo` — the stock/ETF universe roster (`symbol`/`name`/`industry_category`/
# `security_type`), `whole_market_ok=True`, PUBLIC tier (`sources/finmind/technical.py`'s own
# `_DatasetConfig`). Ingested so `get_sector_heatmap` can join each symbol's industry
# classification against the already-ingested `PRICE_EOD_JOB` closes without a per-symbol live
# fetch — the same "whole-market dataset, read back not fetched live" rationale
# `MARKET_VALUE_JOB`/`MARKET_VALUE_WEIGHT_JOB` document above. Cron slot: no other
# `TimingClass.REFERENCE` FinMind dataset is on a scheduled cron today (`theme_industry_seed`/
# `theme_tier_seed`/`us_symbol_universe` are the only precedents, all monthly) — a roster this
# slow-changing (new listings/delistings/industry reclassification) does not need daily
# refresh, but "once daily, off-peak" is chosen over monthly so a same-day new listing's
# industry classification is available to `get_sector_heatmap` the very next post-close read,
# not up to a month later. Scheduled once daily at 06:00 Asia/Taipei — well before the trading
# day's own 17:40+ post-close pulls, so it never competes with them for FinMind quota, and any
# reclassification lands before the day's `get_sector_heatmap` calls need it.
STOCK_INFO_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockInfo",
    capability=Capability.STOCK_INFO,
    finmind_update_time=_REFERENCE_ROSTER_UPDATE_TIME,
    cron_hint="06:00 Asia/Taipei (once daily, off-peak — see job docstring for cadence rationale)",
    row_model=StockInfoRow,
)

# `TaiwanStockHoldingSharesPer` (股權持股分級表) — Sponsor-tier, `whole_market_ok=True`
# (`sources/finmind/chip_institutional.py`'s own `_DatasetConfig`), documented weekly cadence
# (published for each week's final trading day, mirroring TDCC's own free 集保戶股權分散表 the
# same underlying figures ultimately come from). Ingested so a large-holder-tier accumulation
# ranking (大戶加碼股: which symbols grew their >=200/400/800/1,000-張 tier share fastest over
# 1-4 weeks) can be computed by diffing several weeks' already-ingested snapshots, never a
# per-symbol live fan-out across the whole ~2,700-symbol universe for a ranking read. Previously
# listed in `STUBBED_DATASETS` as "ingest-weekly" — this is that dataset's actual wiring.
SHAREHOLDING_DISTRIBUTION_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockHoldingSharesPer",
    capability=Capability.SHAREHOLDING_DISTRIBUTION,
    finmind_update_time="weekly, published for each week's final trading day (Sponsor)",
    cron_hint="06:00 Asia/Taipei, weekly (Cadence.WEEKLY in the daemon — see ingest/daemon.py)",
    row_model=ShareholdingDistributionRow,
)

# Every whole-market job spec this phase wires end-to-end, in the same order the daemon fires
# them — the canonical list a caller (the daemon-completeness test, `DAILY_WHOLE_MARKET_JOBS`
# below) walks instead of hand-copying the specs above, so a newly added spec is picked up
# automatically.
ALL_WHOLE_MARKET_JOBS: tuple[WholeMarketJobSpec, ...] = (
    PRICE_EOD_JOB,
    INSTITUTIONAL_FLOW_JOB,
    FINANCIALS_JOB,
    DIVIDENDS_JOB,
    BALANCE_SHEET_JOB,
    CASH_FLOW_JOB,
    MONTH_REVENUE_JOB,
    MARGIN_SHORT_JOB,
    VALUATION_RATIOS_JOB,
    MARKET_VALUE_JOB,
    MARKET_VALUE_WEIGHT_JOB,
    ACTIVE_ETF_HOLDING_JOB,
    ACTIVE_ETF_HOLDING_CHANGE_JOB,
    STOCK_INFO_JOB,
    SHAREHOLDING_DISTRIBUTION_JOB,
)

# `ALL_WHOLE_MARKET_JOBS` minus `SHAREHOLDING_DISTRIBUTION_JOB` — the subset actually safe to
# backfill with the generic one-call-per-calendar-day loop (`ingest/backfill.py::backfill_range`,
# `ingest/preflight.py::run_preflight_if_enabled`). `SHAREHOLDING_DISTRIBUTION_JOB` is weekly and
# only ever returns rows for the EXACT Friday publish date (see `job_shareholding_distribution`'s
# own docstring) — running it through the daily loop means ~5 empty FinMind calls for every 1 real
# one across a multi-year backfill window. `ingest.backfill_cli` uses this subset for its generic
# per-spec sweep and calls `job_shareholding_distribution` directly, once per week, instead.
DAILY_WHOLE_MARKET_JOBS: tuple[WholeMarketJobSpec, ...] = tuple(
    spec for spec in ALL_WHOLE_MARKET_JOBS if spec is not SHAREHOLDING_DISTRIBUTION_JOB
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
    "ingest-reconcile (~08:30 next-morning re-pull)",
    "ingest-monthly: 景氣信號",
    "ingest-event: news/PTT/rebalance/macro/IPO/yfinance (MOPS 重大訊息/內部人股權轉讓事前申報 now "
    "wired as job_material_announcements/job_insider_transfer_declaration below)",
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


def _validate_rows_against(
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


def _validate_rows(
    spec: WholeMarketJobSpec, raw_rows: list[dict[str, object]], run_date: date_
) -> list[dict[str, object]]:
    """`_validate_rows_against` bound to a `WholeMarketJobSpec`'s own `(row_model, dataset)` —
    the FinMind-sourced call sites' convenience wrapper."""
    return _validate_rows_against(spec.row_model, spec.dataset, raw_rows, run_date)


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
    raw_rows: list[dict[str, object]] = (
        cast("list[dict[str, object]]", rows_obj) if isinstance(rows_obj, list) else []
    )
    rows = _validate_rows(spec, raw_rows, run_date)
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
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            ETF_NAV_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
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
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            ETF_EXPENSE_RATIO_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
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


MATERIAL_ANNOUNCEMENTS_DATASET = "material_announcements"


async def job_material_announcements(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily` — MOPS 重大訊息 (material announcement) whole-market feed
    (`sources/mops.py`), cron **daily, off-peak** (see `ingest/daemon.py`'s `SCHEDULED_JOBS`).

    Not FinMind-sourced — `fetch_whole_market_material_announcements` is `sources/mops.py`'s own
    dedicated whole-market fetch function, the same ingest-layer whole-market carve-out
    `job_etf_nav` uses for its own non-FinMind upstream.

    **Forward-only — no backfill is possible.** MOPS's own upstream has no date parameter; every
    call returns only today's live dump (`sources/mops.py::_ANNOUNCEMENTS_CAVEAT`: "not a
    historical archive; announcements roll off the upstream dataset over time"). This job's
    first successful run seeds day 1 of history; there is no historical range to walk backward
    over the way `run_backfill_cli` does for FinMind datasets. See `ingest/__main__.py`'s
    `_tw_gap_fill` for why this job is ALSO called directly from the startup gap-fill path, not
    only from its own daily cron slot — a day missed because the ingest container was down at
    the cron fire time is otherwise permanently lost, since there is nothing upstream to recover
    it from later."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            MATERIAL_ANNOUNCEMENTS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            MATERIAL_ANNOUNCEMENTS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    result = await fetch_whole_market_material_announcements(tls_policy=ctx.tls_policy)
    if _fetch_error_status(result) is RunStatus.FAILED:
        return journal_run(
            MATERIAL_ANNOUNCEMENTS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=result.health.detail,
        )

    raw_rows_obj = result.payload.get("rows", [])
    raw_rows: list[dict[str, object]] = (
        cast("list[dict[str, object]]", raw_rows_obj) if isinstance(raw_rows_obj, list) else []
    )
    rows = _validate_rows_against(
        MaterialAnnouncementRow, MATERIAL_ANNOUNCEMENTS_DATASET, raw_rows, run_date
    )
    rows_written = ctx.store.write_partition(
        MATERIAL_ANNOUNCEMENTS_DATASET, ctx.market.value, run_date, rows
    )
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.CORPORATE_ACTIONS,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    return journal_run(
        MATERIAL_ANNOUNCEMENTS_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=result.health.detail,
    )


INSIDER_TRANSFER_DECLARATION_DATASET = "insider_transfer_declaration"


async def job_insider_transfer_declaration(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily` — MOPS 內部人股權轉讓事前申報 (insider advance-transfer-plan declaration)
    whole-market feed (`sources/mops.py`, `t187ap12_L` only — see
    `InsiderTransferDeclarationRow`'s own docstring for why `t187ap13_L` is out of scope), cron
    **daily, off-peak** (see `ingest/daemon.py`'s `SCHEDULED_JOBS`). Same forward-only-no-backfill
    and startup-gap-fill reasoning as `job_material_announcements` above — see its docstring."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            INSIDER_TRANSFER_DECLARATION_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            INSIDER_TRANSFER_DECLARATION_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    result = await fetch_whole_market_insider_transfer_declarations(tls_policy=ctx.tls_policy)
    if _fetch_error_status(result) is RunStatus.FAILED:
        return journal_run(
            INSIDER_TRANSFER_DECLARATION_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=result.health.detail,
        )

    raw_rows_obj = result.payload.get("rows", [])
    raw_rows: list[dict[str, object]] = (
        cast("list[dict[str, object]]", raw_rows_obj) if isinstance(raw_rows_obj, list) else []
    )
    rows = _validate_rows_against(
        InsiderTransferDeclarationRow, INSIDER_TRANSFER_DECLARATION_DATASET, raw_rows, run_date
    )
    rows_written = ctx.store.write_partition(
        INSIDER_TRANSFER_DECLARATION_DATASET, ctx.market.value, run_date, rows
    )
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.INSIDER_TRANSFER_DECLARATION,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    return journal_run(
        INSIDER_TRANSFER_DECLARATION_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=result.health.detail,
    )


ATTENTION_STOCKS_DATASET = "attention_stocks"


async def job_attention_stocks(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily` — 注意股 (attention-stock) whole-market feed, TWSE `announcement/notice`
    plus TPEX `tpex_trading_warning_information` (`sources/twse_openapi.py`/
    `sources/tpex_openapi.py`), cron **daily, off-peak** (see `ingest/daemon.py`'s
    `SCHEDULED_JOBS`). Not FinMind-sourced — both fetch functions are ingest-only whole-market
    carve-outs, same shape `job_material_announcements` uses for its own non-FinMind upstream.

    **Forward-only — no backfill is possible**, same MOPS caveat: neither upstream takes a date
    parameter, so this job's daily runs are themselves the only history that ever accumulates.
    Both exchanges' rows land in ONE partition per day (`security_type` distinguishes them) —
    the 處置王 board's rolling-window attention-flag count reads this partition series back, so a
    day missed at the scheduled cron fire time is permanently lost, same reasoning
    `ingest/__main__.py`'s `_tw_gap_fill` already gives for the two MOPS jobs — this job is
    called directly from that startup gap-fill path too, not only its own daily cron slot."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            ATTENTION_STOCKS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            ATTENTION_STOCKS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    twse_result = await fetch_whole_market_attention_stocks(tls_policy=ctx.tls_policy)
    tpex_result = await fetch_whole_market_attention_stocks_tpex(tls_policy=ctx.tls_policy)
    twse_failed = _fetch_error_status(twse_result) is RunStatus.FAILED
    tpex_failed = _fetch_error_status(tpex_result) is RunStatus.FAILED
    if twse_failed and tpex_failed:
        return journal_run(
            ATTENTION_STOCKS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=f"twse={twse_result.health.detail}; tpex={tpex_result.health.detail}",
        )

    raw_rows: list[dict[str, object]] = []
    for result, failed in ((twse_result, twse_failed), (tpex_result, tpex_failed)):
        if failed:
            continue
        rows_obj = result.payload.get("rows", [])
        if isinstance(rows_obj, list):
            raw_rows.extend(cast("list[dict[str, object]]", rows_obj))

    rows = _validate_rows_against(AttentionStockRow, ATTENTION_STOCKS_DATASET, raw_rows, run_date)
    rows_written = ctx.store.write_partition(
        ATTENTION_STOCKS_DATASET, ctx.market.value, run_date, rows
    )
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.ATTENTION_STOCKS,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    status = RunStatus.PARTIAL if (twse_failed or tpex_failed) else RunStatus.SUCCESS
    detail = (
        f"twse={twse_result.health.detail}; tpex={tpex_result.health.detail}"
        if (twse_failed or tpex_failed)
        else "ok"
    )
    return journal_run(
        ATTENTION_STOCKS_DATASET, run_date, rows_written=rows_written, status=status, detail=detail
    )


DISPOSITION_EVENTS_DATASET = "disposition_events"


async def job_disposition_events(run_date: date_, ctx: IngestContext) -> JobRun:
    """`ingest-daily` — 處置 (disposition) whole-market feed, TWSE `announcement/punish` plus
    TPEX `tpex_disposal_information`. Same shape, same forward-only/gap-fill reasoning as
    `job_attention_stocks` above — see its docstring."""
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
        return journal_run(
            DISPOSITION_EVENTS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            DISPOSITION_EVENTS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    twse_result = await fetch_whole_market_disposition_events(tls_policy=ctx.tls_policy)
    tpex_result = await fetch_whole_market_disposition_events_tpex(tls_policy=ctx.tls_policy)
    twse_failed = _fetch_error_status(twse_result) is RunStatus.FAILED
    tpex_failed = _fetch_error_status(tpex_result) is RunStatus.FAILED
    if twse_failed and tpex_failed:
        return journal_run(
            DISPOSITION_EVENTS_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.FAILED,
            detail=f"twse={twse_result.health.detail}; tpex={tpex_result.health.detail}",
        )

    raw_rows: list[dict[str, object]] = []
    for result, failed in ((twse_result, twse_failed), (tpex_result, tpex_failed)):
        if failed:
            continue
        rows_obj = result.payload.get("rows", [])
        if isinstance(rows_obj, list):
            raw_rows.extend(cast("list[dict[str, object]]", rows_obj))

    rows = _validate_rows_against(
        DispositionEventRow, DISPOSITION_EVENTS_DATASET, raw_rows, run_date
    )
    rows_written = ctx.store.write_partition(
        DISPOSITION_EVENTS_DATASET, ctx.market.value, run_date, rows
    )
    await ctx.data_update_publisher.publish_data_updated(
        capability=Capability.DISPOSITION_EVENTS,
        market=ctx.market,
        as_of=run_date.isoformat(),
        scope="whole-market",
    )
    status = RunStatus.PARTIAL if (twse_failed or tpex_failed) else RunStatus.SUCCESS
    detail = (
        f"twse={twse_result.health.detail}; tpex={tpex_result.health.detail}"
        if (twse_failed or tpex_failed)
        else "ok"
    )
    return journal_run(
        DISPOSITION_EVENTS_DATASET,
        run_date,
        rows_written=rows_written,
        status=status,
        detail=detail,
    )


ETF_AUM_DATASET = "etf_aum"

# yfinance has no whole-market ETF endpoint (same gap `ingest/us_jobs.py::job_us_fundamentals`
# documents for US fundamentals) — this job's per-symbol loop is the accepted fan-out exception,
# paced identically to that job's own throttle to avoid the same rate-limiting failure mode
# (455 "Too Many Requests" in under 4 minutes, confirmed empirically there with no delay).
_YFINANCE_THROTTLE_SECONDS = 0.5


def _latest_etf_nav_symbols(ctx: IngestContext) -> tuple[str, ...]:
    """The ETF universe to loop AUM over — read from the already-ingested `etf_nav` partition
    (TWSE's own whole-market ETF roster, `job_etf_nav`) rather than a hardcoded list, mirroring
    `ingest/us_universe.py::latest_us_symbols`'s "read a roster ingest already wrote" pattern.
    Empty (never a crash) if `job_etf_nav` hasn't run yet."""
    dates = ctx.store.list_partition_dates(ETF_NAV_DATASET, ctx.market.value)
    if not dates:
        return ()
    raw_rows = ctx.store.read_partition(ETF_NAV_DATASET, ctx.market.value, max(dates))
    field_names = set(EtfNavRow.model_fields)
    symbols = {
        str(row["symbol"]) for row in raw_rows if "symbol" in {k for k in row if k in field_names}
    }
    return tuple(sorted(symbols))


def _derived_etf_aum_rows(ctx: IngestContext, run_date: date_) -> list[dict[str, object]]:
    """AUM = `units_outstanding x prior_day_nav`, from the already-ingested `etf_nav` partition
    (TWSE MIS's bulk `all_etf.txt`, `job_etf_nav`) — no extra request, and it covers every
    listed ETF including the bond ETFs Yahoo has no `totalAssets` for. `prior_day_nav` is the
    issuer's settled unit NAV, not the intraday estimate. A row missing either input, or with a
    non-positive one, is skipped (left for the Yahoo fallback), never written as a guess."""
    dates = ctx.store.list_partition_dates(ETF_NAV_DATASET, ctx.market.value)
    if not dates:
        return []
    rows: list[dict[str, object]] = []
    for raw in ctx.store.read_partition(ETF_NAV_DATASET, ctx.market.value, max(dates)):
        symbol, units, nav = (
            raw.get("symbol"),
            raw.get("units_outstanding"),
            raw.get("prior_day_nav"),
        )
        if not isinstance(symbol, str) or not isinstance(units, int | float):
            continue
        if not isinstance(nav, int | float) or units <= 0 or nav <= 0:
            continue
        rows.append(
            {
                "symbol": symbol,
                "market": ctx.market.value,
                "as_of": run_date.isoformat(),
                "aum": float(units) * float(nav),
            }
        )
    return rows


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

    Symbol universe comes from `_latest_etf_nav_symbols` (the already-ingested `etf_nav` roster),
    never a hardcoded list — an empty universe (that job hasn't run yet) is a graceful skip.
    One fallback symbol's failure never aborts the run: it's counted, journaled as `PARTIAL` if
    any symbol failed but some rows still landed, `FAILED` only if none did."""
    if not ctx.leader.is_leader():
        return journal_run(
            ETF_AUM_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )

    symbols = _latest_etf_nav_symbols(ctx)
    if not symbols:
        return journal_run(
            ETF_AUM_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="no etf_nav roster yet — run job_etf_nav first",
        )

    raw_rows = _derived_etf_aum_rows(ctx, run_date)
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


__all__ = [
    "ACTIVE_ETF_HOLDING_CHANGE_JOB",
    "ACTIVE_ETF_HOLDING_JOB",
    "ALL_WHOLE_MARKET_JOBS",
    "BALANCE_SHEET_JOB",
    "CASH_FLOW_JOB",
    "DAILY_WHOLE_MARKET_JOBS",
    "DIVIDENDS_JOB",
    "ETF_AUM_DATASET",
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
    "WholeMarketJobSpec",
    "job_active_etf_holding_changes",
    "job_active_etf_holdings",
    "job_balance_sheet",
    "job_cash_flow",
    "job_dividends",
    "job_etf_aum",
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
