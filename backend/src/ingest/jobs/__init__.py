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

from ingest.jobs.disclosures import (
    INSIDER_TRANSFER_DECLARATION_DATASET,
    MATERIAL_ANNOUNCEMENTS_DATASET,
    job_insider_transfer_declaration,
    job_material_announcements,
)
from ingest.jobs.etf import (
    ETF_AUM_DATASET,
    ETF_EXPENSE_RATIO_DATASET,
    ETF_NAV_DATASET,
    job_etf_aum,
    job_etf_expense_ratio,
    job_etf_nav,
)
from ingest.jobs.risk_lists import (
    ATTENTION_STOCKS_DATASET,
    DISPOSITION_EVENTS_DATASET,
    job_attention_stocks,
    job_disposition_events,
)
from ingest.jobs.shareholding import (
    job_shareholding_distribution,
)
from ingest.jobs.specs import (
    ACTIVE_ETF_HOLDING_CHANGE_JOB,
    ACTIVE_ETF_HOLDING_JOB,
    ALL_WHOLE_MARKET_JOBS,
    BALANCE_SHEET_JOB,
    CASH_FLOW_JOB,
    DAILY_WHOLE_MARKET_JOBS,
    DIVIDENDS_JOB,
    FINANCIALS_JOB,
    INSTITUTIONAL_FLOW_JOB,
    MARGIN_SHORT_JOB,
    MARKET_VALUE_JOB,
    MARKET_VALUE_WEIGHT_JOB,
    MONTH_REVENUE_JOB,
    PRICE_EOD_JOB,
    SHAREHOLDING_DISTRIBUTION_JOB,
    STOCK_INFO_JOB,
    STUBBED_DATASETS,
    VALUATION_RATIOS_JOB,
    WholeMarketJobSpec,
)
from ingest.jobs.whole_market import (
    job_active_etf_holding_changes,
    job_active_etf_holdings,
    job_balance_sheet,
    job_cash_flow,
    job_dividends,
    job_financials,
    job_institutional_flow,
    job_margin_short,
    job_market_value,
    job_market_value_weight,
    job_month_revenue,
    job_price_eod,
    job_stock_info,
    job_valuation_ratios,
    run_whole_market_job,
)

__all__ = [
    "ACTIVE_ETF_HOLDING_CHANGE_JOB",
    "ACTIVE_ETF_HOLDING_JOB",
    "ALL_WHOLE_MARKET_JOBS",
    "ATTENTION_STOCKS_DATASET",
    "BALANCE_SHEET_JOB",
    "CASH_FLOW_JOB",
    "DAILY_WHOLE_MARKET_JOBS",
    "DISPOSITION_EVENTS_DATASET",
    "DIVIDENDS_JOB",
    "ETF_AUM_DATASET",
    "ETF_EXPENSE_RATIO_DATASET",
    "ETF_NAV_DATASET",
    "FINANCIALS_JOB",
    "INSIDER_TRANSFER_DECLARATION_DATASET",
    "INSTITUTIONAL_FLOW_JOB",
    "MARGIN_SHORT_JOB",
    "MARKET_VALUE_JOB",
    "MARKET_VALUE_WEIGHT_JOB",
    "MATERIAL_ANNOUNCEMENTS_DATASET",
    "MONTH_REVENUE_JOB",
    "PRICE_EOD_JOB",
    "SHAREHOLDING_DISTRIBUTION_JOB",
    "STOCK_INFO_JOB",
    "STUBBED_DATASETS",
    "VALUATION_RATIOS_JOB",
    "WholeMarketJobSpec",
    "job_active_etf_holding_changes",
    "job_active_etf_holdings",
    "job_attention_stocks",
    "job_balance_sheet",
    "job_cash_flow",
    "job_disposition_events",
    "job_dividends",
    "job_etf_aum",
    "job_etf_expense_ratio",
    "job_etf_nav",
    "job_financials",
    "job_insider_transfer_declaration",
    "job_institutional_flow",
    "job_margin_short",
    "job_market_value",
    "job_market_value_weight",
    "job_material_announcements",
    "job_month_revenue",
    "job_price_eod",
    "job_shareholding_distribution",
    "job_stock_info",
    "job_valuation_ratios",
    "run_whole_market_job",
]
