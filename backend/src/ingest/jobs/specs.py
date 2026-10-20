"""The whole-market job table: `WholeMarketJobSpec` and every FinMind dataset it describes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as time_

import structlog
from pydantic import BaseModel

from schemas.capabilities import (
    ActiveEtfHoldingChangeRow,
    ActiveEtfHoldingRow,
    DividendEvent,
    EODBar,
    FinancialStatementLine,
    InstitutionalFlowRow,
    MarginShortRow,
    MarketValueRow,
    MarketValueWeightRow,
    MonthRevenueRow,
    ShareholdingDistributionRow,
    StockInfoRow,
    ValuationRatiosRow,
)
from schemas.enums import Capability

_CRON_2130_STATEMENT_PULLS = (
    "21:30 Asia/Taipei (with the other statement pulls; catches same-day filings)"
)

logger = structlog.get_logger(__name__)


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
    model any reader (`_rows_from_store` in `tools/market/`, `fetch_rows` in
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
    # When the dataset is published, Taipei time, if later than the usual 17:30 post-close
    # publish. A gap-fill run before this time targets the previous trading day, so it does not
    # fetch a day the upstream has not published yet (and record it as an empty placeholder).
    publish_after: time_ | None = None
    # A current-state snapshot with no per-day history upstream (the stock roster): a gap-fill
    # fetches only the latest day. Walking back would file today's roster under every past date.
    snapshot_only: bool = False


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
    cron_hint=_CRON_2130_STATEMENT_PULLS,
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
    cron_hint=_CRON_2130_STATEMENT_PULLS,
    row_model=FinancialStatementLine,
)


CASH_FLOW_JOB = WholeMarketJobSpec(
    dataset="TaiwanStockCashFlowsStatement",
    capability=Capability.CASH_FLOW_STATEMENT,
    finmind_update_time=_IRREGULAR_PER_FILING_UPDATE_TIME,
    cron_hint=_CRON_2130_STATEMENT_PULLS,
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
    publish_after=time_(23, 30),
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
    publish_after=time_(23, 30),
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
    snapshot_only=True,
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
