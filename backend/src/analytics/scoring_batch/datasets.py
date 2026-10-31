"""Dataset names and lookback windows the scoring batches read."""

from __future__ import annotations

import structlog

from analytics.fundamentals import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIALS_DATASET,
)
from schemas.enums import Market

logger = structlog.get_logger(__name__)


STOCK_SCORE_DATASET = "stock_scores"


# Upstream derived datasets this job reads back. Local read-key constants (matching the
# decoupling convention `analytics/fundamentals.py` and `analytics/screener/batch.py` already
# use) rather than importing the ingest job specs, so this compute layer does not bind to the
# scheduler's wiring.
PRICE_EOD_DATASET = "TaiwanStockPrice"


INSTITUTIONAL_FLOW_DATASET = "TaiwanStockInstitutionalInvestorsBuySell"


# The three ETF-specific derived datasets (`ingest/jobs.py`'s own dataset-name constants,
# duplicated here as local read-key constants rather than imported — same decoupling convention
# `PRICE_EOD_DATASET`/`INSTITUTIONAL_FLOW_DATASET` above already use, so this compute layer
# never binds to the scheduler's wiring).
ETF_NAV_DATASET = "etf_nav"


ETF_EXPENSE_RATIO_DATASET = "etf_expense_ratio"


ACTIVE_ETF_HOLDING_DATASET = "TaiwanStockActiveETFHolding"


ACTIVE_ETF_HOLDING_CHANGE_DATASET = "TaiwanStockActiveETFHoldingChange"


# `STOCK_INFO_JOB`'s dataset — same local-read-key-constant decoupling convention as the others
# above. Read back here so a symbol whose trailing price/flow history is still in the lookback
# window but has dropped out of the *current* reference roster (a real corporate action — merger,
# cash-out, delisting; e.g. 2888 新光金控's 2025 merger into 台新新光金控) never reaches the score
# universe. Without this, `job_scoring_batch` keeps producing a `ScoreResult` for a symbol
# `get_symbol_info` can no longer name, since that tool's roster join is the only place "still
# listed" is ever checked (`tools/market/symbols.py::GetSymbolInfoTool`'s own docstring) — the
# leaderboard would show a bare code with no name forever, not just for one stale day.
STOCK_INFO_DATASET = "TaiwanStockInfo"


# US counterparts (`ingest/us_jobs.py`'s own dataset-name constants, duplicated here as local
# read-key constants for the same decoupling reason as the TW constants above). US has no
# institutional-flow/market-value/ETF-nav/active-ETF/stock-roster datasets ingested at all — those
# TW-only factors/dimensions are simply never computed for US (no US branch calls them). In their
# place, `job_scoring_batch`/`job_trend_score_batch` read these four US-native datasets (also
# `ingest/us_jobs.py`-written, via `job_us_fundamentals`'s extended fan-out) for the US-only
# `analyst_revision`/`institutional_ownership`/`insider_sentiment`/`sector_momentum`
# factor/dimensions — see `analytics/score.py::compute_us_stock_factor_breakdown` and
# `analytics/trend_score.py`'s US dimension functions.
US_PRICE_EOD_DATASET = "us_price_eod"


US_FINANCIALS_DATASET = "us_financials"


US_BALANCE_SHEET_DATASET = "us_balance_sheet"


US_CASH_FLOW_DATASET = "us_cash_flow"


US_ANALYST_CONSENSUS_DATASET = "us_analyst_consensus"


US_INSTITUTIONAL_HOLDERS_DATASET = "us_institutional_holders"


US_INSIDER_ACTIVITY_DATASET = "us_insider_activity"


US_SECTOR_CLASS_DATASET = "us_sector_class"


# FinMind investor-category labels the revenue/flow factor's chip sub-signal sums.
FLOW_INVESTORS = ("Foreign_Investor", "Investment_Trust")


def price_eod_dataset(market: Market) -> str:
    return US_PRICE_EOD_DATASET if market is Market.US else PRICE_EOD_DATASET


def financials_datasets(market: Market) -> tuple[str, str, str]:
    """`(income_statement, balance_sheet, cash_flow)` dataset names for `market` — see
    `US_FINANCIALS_DATASET`'s own comment for why only these three (not institutional-flow/
    market-value/ETF datasets) have a US counterpart at all."""
    if market is Market.US:
        return US_FINANCIALS_DATASET, US_BALANCE_SHEET_DATASET, US_CASH_FLOW_DATASET
    return FINANCIALS_DATASET, BALANCE_SHEET_DATASET, CASH_FLOW_DATASET


# 12-1 momentum needs 253 trading days; the Minervini trend template needs 252 plus the MA200
# slope check reaches back another ~22 — 260 is the same comfortable margin
# `analytics/screener/batch.py` already reads for its own technical-event window.
PRICE_LOOKBACK_DAYS = 260


# `net_buy_20d_trend`'s own convention: callers pass the trailing 20-trading-day slice.
FLOW_LOOKBACK_DAYS = 20


# Financial-statement partitions are written on their filing/ingest day, not on the statement's
# own period-end — so a symbol's most recent quarters can sit in any of many past daily
# partitions. ~400 trading days (~1.5 years) is a generous-but-bounded window that comfortably
# spans several quarterly filing cycles without reading the store's entire history every night.
FINANCIALS_LOOKBACK_DAYS = 400


# The market-cap snapshot is a daily whole-market pull; a short lookback absorbs an occasional
# missed/gapped day without reading a long history for what is really "yesterday's number".
MARKET_VALUE_LOOKBACK_DAYS = 10


# A month's revenue is usable only from the 10th of the following month, and its YoY needs the
# same month a year earlier: the latest published month plus its year-ago is 13-14 months back.
# Rows land in the partition of the 1st (or next trading day) of the month after, so 300 trading
# days (~14 months) covers both with slack.
MONTH_REVENUE_LOOKBACK_DAYS = 300


# `etf_nav`/active-ETF-holding snapshots are also daily whole-market pulls — same short-lookback
# reasoning as `MARKET_VALUE_LOOKBACK_DAYS`.
ETF_NAV_LOOKBACK_DAYS = 10


ACTIVE_ETF_HOLDING_LOOKBACK_DAYS = 10


# SITCA's fee report only refreshes monthly (`job_etf_expense_ratio`'s own docstring) — this
# window must comfortably span a full month so "yesterday's number" is still available even
# right before the next monthly pull lands.
ETF_EXPENSE_RATIO_LOOKBACK_DAYS = 40


# `net_buy_20d_trend`'s own convention, reused for the holdings-turnover proxy.
HOLDINGS_TURNOVER_LOOKBACK_DAYS = 20


# `job_us_fundamentals` runs weekly (`ingest/us_jobs.py`'s own cadence) — same
# generous-but-bounded reasoning as `FINANCIALS_LOOKBACK_DAYS` so a symbol's most recent
# analyst_consensus/institutional_holders/insider_activity/sector_class pull is comfortably
# within the read window even right before the next weekly run lands.
US_ANALYST_DATA_LOOKBACK_DAYS = 40


# Sector-relative momentum's own convention (Moskowitz & Grinblatt) — trailing 3 months.
SECTOR_MOMENTUM_LOOKBACK_DAYS = 63


TREND_SCORE_DATASET = "trend_scores"


# `Capability.MARGIN_SHORT`'s FinMind dataset (`sources/finmind/chip_institutional.py`'s own
# `_DatasetConfig`) — confirmed `whole_market_ok=True` there, i.e. it DOES have a documented
# omit-`data_id` whole-market request form, unlike `chip_broker` below. The `ingest/jobs.py`
# `STUBBED_DATASETS` comment ("ingest-daily-late: 融資融券/... 21:05") only means no
# `WholeMarketJobSpec`/cron entry has been wired for it yet — the adapter-level capability is
# real and whole-market-capable. Rather than wait on a scheduler-wiring phase, this batch reads
# it directly via `ctx.finmind.fetch_whole_market` itself (the same call `run_whole_market_job`
# would make), mirroring `run_whole_market_job`'s validate-then-read-back shape but writing
# straight into this batch's own per-symbol grouping instead of a `ParquetStore` partition —
# a deliberate scope narrowing (this batch's own trailing read window, not a persisted
# whole-market partition another reader could reuse) since standing up the full ingest job/cron
# wiring is out of this batch's scope.
MARGIN_SHORT_DATASET = "TaiwanStockMarginPurchaseShortSale"


# `net_buy_20d_trend`'s own convention — margin/short balance is read over the same trailing
# window so `margin_credit_trend_score`'s day-over-day delta always has at least 2 rows to diff.
MARGIN_SHORT_LOOKBACK_DAYS = 5


# `price_change_pct`'s own minimum: at least 2 closes (today + one prior day) to diff a change.
PRICE_CHANGE_MIN_BARS = 2


# TAIEX's own daily closes, from the SAME whole-market `PRICE_EOD_DATASET` partition
# (`sources/finmind/technical.py::_map_eod_bars` confirms `TAIEX` rides along in the raw
# whole-market payload) — but `scoreable_symbol_mask` (four-digit-or-00-prefixed) excludes it, so
# it must be read back unfiltered, separately from `_bars_by_symbol`'s per-stock grouping.
TAIEX_SYMBOL = "TAIEX"


_TAIEX_CORRELATION_LOOKBACK_DAYS = 60  # comfortably above `taiex_correlation_score`'s own


# `_SECTOR_ROTATION_WINDOW_TRADING_DAYS` in
# `tools/market/sector_rotation.py::GetSectorRotationTool` — kept
# as a local duplicate constant per this codebase's stated convention (see the module-level
# rationale in `job_trend_score_batch`'s own docstring: `analytics/` may not import `tools/`,
# the dependency-rule direction backend/CLAUDE.md §3 enforces, so the small rank-computation
# arithmetic that tool performs is duplicated here rather than imported).
_SECTOR_ROTATION_WINDOW_TRADING_DAYS = 10


# The `FundamentalMetrics` attribute names the quality/value factors z-score — kept as local
# tuples (not imported from `analytics.score`'s private `_QUALITY_SUB_METRICS`/
# `_VALUE_SUB_METRICS`) so this batch module depends only on `score.py`'s public surface.
QUALITY_ATTRS: tuple[str, ...] = (
    "roe",
    "debt_to_equity",
    "earnings_variability",
    "roic",
    "gross_profitability",
)


VALUE_ATTRS: tuple[str, ...] = ("ev_ebitda", "fcf_yield", "pe", "pb")


MA_WINDOWS: tuple[int, ...] = (50, 150, 200)
