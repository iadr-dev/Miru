"""The daily scoring batch — computes the full multi-factor `ScoreResult` for **every** TW symbol
that day's ingested price/flow/fundamental data covers, for both horizon profiles, and upserts
every row into the relational `stock_scores` cache (`analytics/scoring_store.py`).

This is what makes both the per-symbol detail-page lookup and the whole-universe leaderboard fast
reads: the expensive part — assembling each symbol's series, building the day's cross-sectional
comparison universes (`winsorized_percentile`/`zscore` need the *whole* day's values to rank
against, not one symbol at a time), and blending the composite — happens once here; the two read
seams (`get_cached_score`/`get_leaderboard`) just look the finished rows back up.

**No `top_n`/`limit` anywhere in this module.** The whole point of a cross-sectional score is that
every symbol's percentile depends on the full comparison universe, so scoring only a subset would
both under-cover the leaderboard and silently bias every symbol's rank against a truncated peer
group. The batch iterates every symbol present in the day's price, institutional-flow, or
financial-statement/market-value data — exactly the same universe-discovery step that builds the
cross-sectional `momentum_universe`/`flow_universe`/`quality_universe`/`value_universe` sequences
`compute_factor_breakdown` needs. Top-N slicing belongs only at read time, in
`scoring_store.get_leaderboard`.

Modeled on `analytics/screener/batch.py`'s shape: leader- and calendar-gated at the call site, a
journaled run, reads the derived Parquet store the upstream whole-market jobs already wrote
(never a live per-symbol fetch), and writes through `IngestContext.session_factory` — the same
seam the theme layer's monthly jobs use for their relational writes — rather than the Parquet
store, since the score cache is relational (see `scoring_store.py`'s docstring for why).
"""

from __future__ import annotations

import statistics
from calendar.trading_days import TradingDayCalendar
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date as date_
from typing import cast

import pandas as pd
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.fundamentals import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIALS_DATASET,
    MARKET_VALUE_DATASET,
    FundamentalMetrics,
    compute_fundamental_metrics,
    parse_market_value_rows,
    parse_statement_lines,
)
from analytics.indicators import PriceBar, sma
from analytics.models import (
    EtfScoreBreakdown,
    ScoreProfile,
    ScoreResult,
    StockScoreBreakdown,
    TrendDimensionResult,
    TrendScoreResult,
    UsStockScoreBreakdown,
)
from analytics.score import (
    EtfFactorInputs,
    FactorInputs,
    average_dollar_volume,
    build_score_result,
    compute_etf_factor_breakdown,
    compute_stock_factor_breakdown,
    compute_us_stock_factor_breakdown,
    eps_trend_delta,
    net_buy_20d_trend,
    price_target_upside,
    twelve_minus_one_momentum,
)
from analytics.scoring_store import upsert_score
from analytics.symbol_universe import SymbolType, classify_symbol_type, scoreable_symbol_mask
from analytics.trend_score import (
    US_DIMENSION_WEIGHTS,
    TrendDimension,
    analyst_revision_trend_score,
    blend_trend_composite,
    chip_flow_trend_score,
    insider_sentiment_trend_score,
    institutional_ownership_trend_score,
    margin_credit_trend_score,
    sector_momentum_trend_score,
    sector_rotation_trend_score,
    taiex_correlation_score,
    technical_trend_score,
)
from analytics.trend_score_store import upsert_trend_score
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run
from schemas.capabilities import (
    ActiveEtfHoldingChangeRow,
    ActiveEtfHoldingRow,
    AnalystConsensus,
    EtfExpenseInfo,
    EtfNavRow,
    FinancialStatementLine,
    InsiderActivityRow,
    InstitutionalFlowRow,
    InstitutionalHolderRow,
    MarginShortRow,
    MarketValueRow,
    SectorClassification,
    StockInfoRow,
)
from schemas.enums import Capability, Market
from schemas.source import HealthStatus

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
# listed" is ever checked (`tools/market_tools.py::GetSymbolInfoTool`'s own docstring) — the
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
_FLOW_INVESTORS = ("Foreign_Investor", "Investment_Trust")


def _price_eod_dataset(market: Market) -> str:
    return US_PRICE_EOD_DATASET if market is Market.US else PRICE_EOD_DATASET


def _financials_datasets(market: Market) -> tuple[str, str, str]:
    """`(income_statement, balance_sheet, cash_flow)` dataset names for `market` — see
    `US_FINANCIALS_DATASET`'s own comment for why only these three (not institutional-flow/
    market-value/ETF datasets) have a US counterpart at all."""
    if market is Market.US:
        return US_FINANCIALS_DATASET, US_BALANCE_SHEET_DATASET, US_CASH_FLOW_DATASET
    return FINANCIALS_DATASET, BALANCE_SHEET_DATASET, CASH_FLOW_DATASET


# 12-1 momentum needs 253 trading days; the Minervini trend template needs 252 plus the MA200
# slope check reaches back another ~22 — 260 is the same comfortable margin
# `analytics/screener/batch.py` already reads for its own technical-event window.
_PRICE_LOOKBACK_DAYS = 260
# `net_buy_20d_trend`'s own convention: callers pass the trailing 20-trading-day slice.
_FLOW_LOOKBACK_DAYS = 20
# Financial-statement partitions are written on their filing/ingest day, not on the statement's
# own period-end — so a symbol's most recent quarters can sit in any of many past daily
# partitions. ~400 trading days (~1.5 years) is a generous-but-bounded window that comfortably
# spans several quarterly filing cycles without reading the store's entire history every night.
_FINANCIALS_LOOKBACK_DAYS = 400
# The market-cap snapshot is a daily whole-market pull; a short lookback absorbs an occasional
# missed/gapped day without reading a long history for what is really "yesterday's number".
_MARKET_VALUE_LOOKBACK_DAYS = 10
# `etf_nav`/active-ETF-holding snapshots are also daily whole-market pulls — same short-lookback
# reasoning as `_MARKET_VALUE_LOOKBACK_DAYS`.
_ETF_NAV_LOOKBACK_DAYS = 10
_ACTIVE_ETF_HOLDING_LOOKBACK_DAYS = 10
# SITCA's fee report only refreshes monthly (`job_etf_expense_ratio`'s own docstring) — this
# window must comfortably span a full month so "yesterday's number" is still available even
# right before the next monthly pull lands.
_ETF_EXPENSE_RATIO_LOOKBACK_DAYS = 40
# `net_buy_20d_trend`'s own convention, reused for the holdings-turnover proxy.
_HOLDINGS_TURNOVER_LOOKBACK_DAYS = 20
# `job_us_fundamentals` runs weekly (`ingest/us_jobs.py`'s own cadence) — same
# generous-but-bounded reasoning as `_FINANCIALS_LOOKBACK_DAYS` so a symbol's most recent
# analyst_consensus/institutional_holders/insider_activity/sector_class pull is comfortably
# within the read window even right before the next weekly run lands.
_US_ANALYST_DATA_LOOKBACK_DAYS = 40
# Sector-relative momentum's own convention (Moskowitz & Grinblatt) — trailing 3 months.
_SECTOR_MOMENTUM_LOOKBACK_DAYS = 63

# ---------------------------------------------------------------------------------------------
# 趨勢評分 (trend score) batch — reads the SAME price/flow frames above plus two more datasets,
# and writes `analytics/trend_score_store.py`'s `trend_scores` table. See
# `job_trend_score_batch`'s own docstring for the full read-path design (which of the six
# dimensions are batch-computed here vs. deferred to the tool's on-demand `run()`).
# ---------------------------------------------------------------------------------------------

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
_MARGIN_SHORT_LOOKBACK_DAYS = 5
# `price_change_pct`'s own minimum: at least 2 closes (today + one prior day) to diff a change.
_PRICE_CHANGE_MIN_BARS = 2

# TAIEX's own daily closes, from the SAME whole-market `PRICE_EOD_DATASET` partition
# (`sources/finmind/technical.py::_map_eod_bars` confirms `TAIEX` rides along in the raw
# whole-market payload) — but `scoreable_symbol_mask` (four-digit-or-00-prefixed) excludes it, so
# it must be read back unfiltered, separately from `_bars_by_symbol`'s per-stock grouping.
_TAIEX_SYMBOL = "TAIEX"
_TAIEX_CORRELATION_LOOKBACK_DAYS = 60  # comfortably above `taiex_correlation_score`'s own
# `_TAIEX_CORR_MIN_POINTS + 1` (11) floor, without re-reading the full 260-day momentum window
# this dimension has no use for.

# `_SECTOR_ROTATION_WINDOW_TRADING_DAYS` in `tools/market_tools.py::GetSectorRotationTool` — kept
# as a local duplicate constant per this codebase's stated convention (see the module-level
# rationale in `job_trend_score_batch`'s own docstring: `analytics/` may not import `tools/`,
# the dependency-rule direction backend/CLAUDE.md §3 enforces, so the small rank-computation
# arithmetic that tool performs is duplicated here rather than imported).
_SECTOR_ROTATION_WINDOW_TRADING_DAYS = 10

# The `FundamentalMetrics` attribute names the quality/value factors z-score — kept as local
# tuples (not imported from `analytics.score`'s private `_QUALITY_SUB_METRICS`/
# `_VALUE_SUB_METRICS`) so this batch module depends only on `score.py`'s public surface.
_QUALITY_ATTRS: tuple[str, ...] = (
    "roe",
    "debt_to_equity",
    "earnings_variability",
    "roic",
    "gross_profitability",
)
_VALUE_ATTRS: tuple[str, ...] = ("ev_ebitda", "fcf_yield", "pe", "pb")

_MA_WINDOWS: tuple[int, ...] = (50, 150, 200)


def _active_roster_symbols(ctx: IngestContext, market_str: str) -> frozenset[str] | None:
    """Every symbol the *latest* `STOCK_INFO_JOB` roster partition actually lists — the same
    "latest real date" lookup `tools/market_tools.py::_latest_dataset_date` performs, inlined here
    rather than imported since `analytics/` may not depend on `tools/` (dependency rule, backend/
    CLAUDE.md §3). Returns `None`, not an empty set, when the roster has never been ingested for
    this market — the caller must treat that as "no roster to filter against" and skip filtering,
    never as "the roster is empty" (which would silently drop every symbol from the score
    universe)."""
    partition_dates = ctx.store.list_partition_dates(STOCK_INFO_DATASET, market_str)
    if not partition_dates:
        return None
    roster_date = max(partition_dates)
    raw_roster = ctx.store.read_partition(STOCK_INFO_DATASET, market_str, roster_date)
    field_names = set(StockInfoRow.model_fields)
    roster = [
        StockInfoRow.model_validate({k: v for k, v in row.items() if k in field_names})
        for row in raw_roster
    ]
    return frozenset(row.symbol for row in roster)


def _calendar_for(ctx: IngestContext) -> TradingDayCalendar:
    """`ctx.us_calendar` for a `Market.US`-scoped context, `ctx.calendar` (TW) otherwise — TW and
    US holiday sets diverge (`ingest/context.py`'s own `us_calendar` field comment), so this
    batch must never gate a US run against the TW calendar or vice versa."""
    return ctx.us_calendar if ctx.market is Market.US else ctx.calendar


def _trailing_trading_days(ctx: IngestContext, run_date: date_, count: int) -> tuple[date_, ...]:
    """The `count` trading days ending at (and including) `run_date`, via the trading-day
    calendar so the window skips weekends/holidays rather than hand-rolling date math — same
    helper shape `screener/batch.py` uses."""
    calendar = _calendar_for(ctx)
    start = calendar.shift_business_days(run_date, -(count - 1))
    return calendar.business_days_between(start, run_date)


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


# A composite momentum/quality/value score is not a meaningful product for a short-lived,
# leverage-derivative warrant series — and scoring the full warrant-inclusive universe is also
# what pushes this batch's per-symbol working set (bars/MA-series/signal dicts held for the
# whole universe at once, see `_score_and_persist_universe`) past the ~7.6GB it OOM-killed at.
# The classification rule (and its measured split) lives in `analytics/symbol_universe.py`,
# shared with every other reader of the raw `TaiwanStockPrice` universe.
_PRICE_FRAME_COLUMNS: tuple[str, ...] = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
)
_FLOW_FRAME_COLUMNS: tuple[str, ...] = ("symbol", "trade_date", "investor", "buy", "sell")


def _bars_by_symbol(
    price_frame: pd.DataFrame, market: Market = Market.TW
) -> dict[str, tuple[PriceBar, ...]]:
    """Group the price window's rows by symbol and build each an ascending-by-date `PriceBar`
    series, from the columnar `read_range_frame` read (see that method's own docstring for why
    this reads a `DataFrame`, not `list[dict]` — this whole-market, 260-trading-day window is
    exactly the read `read_range`'s per-row `dict` boxing OOMs on). A row with no usable close
    is dropped rather than faked (matches the ingest mapper and `screener/batch.py`'s own
    `_bars_for_symbol`)."""
    bars_by_symbol: dict[str, tuple[PriceBar, ...]] = {}
    projected = cast("pd.DataFrame", price_frame.loc[:, list(_PRICE_FRAME_COLUMNS)])
    # Drop warrant/derivative codes before any per-symbol grouping — a vectorized mask over the
    # whole frame, not a per-row Python check, so the excluded ~90% of rows
    # (`analytics.symbol_universe`'s own comment has the measured split) never reach
    # `PriceBar` construction at all. A no-op for US (see `scoreable_symbol_mask`'s own
    # docstring — the ingested US universe has no warrant/derivative pollution to exclude).
    scoreable = scoreable_symbol_mask(cast("pd.Series", projected["symbol"]), market)
    ordered = cast("pd.DataFrame", projected.loc[scoreable].sort_values("trade_date"))
    by_symbol: dict[str, list[PriceBar]] = defaultdict(list)
    # Positional-tuple unpacking (`name=None`), not attribute access: pandas' `itertuples()`
    # has no stub-backed field/row types (no `pandas-stubs` dependency here), so `row.close`
    # style access is unknown to pyright end to end. The explicit `tuple[object, ...]` cast
    # gives every unpacked name a concrete (if erased) type; the column order is guaranteed by
    # `_PRICE_FRAME_COLUMNS`/the `.loc` reorder above.
    price_tuples = cast(
        "Iterable[tuple[object, object, object, object, object, object, object, object]]",
        ordered.itertuples(index=False, name=None),
    )
    for symbol, trade_date, open_, high, low, close, volume, turnover in price_tuples:
        del trade_date  # only used for the sort above; not part of PriceBar
        close_f = _as_float(close)
        high_f = _as_float(high)
        low_f = _as_float(low)
        if close_f is None or close_f <= 0 or high_f is None or low_f is None:
            continue
        by_symbol[str(symbol)].append(
            PriceBar(
                high=high_f,
                low=low_f,
                close=close_f,
                volume=_as_int(volume),
                open=_as_float(open_),
                turnover=_as_float(turnover),
            )
        )
    for symbol, bars in by_symbol.items():
        bars_by_symbol[symbol] = tuple(bars)
    return bars_by_symbol


def _flow_rows_by_symbol(
    flow_frame: pd.DataFrame, market: Market
) -> dict[str, list[InstitutionalFlowRow]]:
    """The `DataFrame` counterpart of `_bars_by_symbol`, same reasoning — see that function's
    docstring."""
    by_symbol: dict[str, list[InstitutionalFlowRow]] = defaultdict(list)
    projected = cast("pd.DataFrame", flow_frame.loc[:, list(_FLOW_FRAME_COLUMNS)])
    # Same warrant/derivative exclusion as `_bars_by_symbol` — see that function's docstring.
    scoreable = scoreable_symbol_mask(cast("pd.Series", projected["symbol"]))
    ordered = cast("pd.DataFrame", projected.loc[scoreable])
    # Positional unpacking — see `_bars_by_symbol`'s docstring for why.
    flow_tuples = cast(
        "Iterable[tuple[object, object, object, object, object]]",
        ordered.itertuples(index=False, name=None),
    )
    for symbol, trade_date, investor, buy, sell in flow_tuples:
        if not isinstance(symbol, str) or not symbol or not isinstance(investor, str):
            continue
        if not isinstance(trade_date, str):
            continue
        by_symbol[symbol].append(
            InstitutionalFlowRow(
                symbol=symbol,
                market=market,
                trade_date=date_.fromisoformat(trade_date),
                investor=investor,
                buy=_as_int(buy),
                sell=_as_int(sell),
            )
        )
    return by_symbol


def _statement_lines_by_symbol(
    rows: Sequence[dict[str, object]],
) -> dict[str, list[FinancialStatementLine]]:
    by_symbol: dict[str, list[FinancialStatementLine]] = defaultdict(list)
    for line in parse_statement_lines(rows):
        by_symbol[line.symbol].append(line)
    return by_symbol


def _latest_market_value_by_symbol(rows: Sequence[dict[str, object]]) -> dict[str, float]:
    latest: dict[str, MarketValueRow] = {}
    for row in parse_market_value_rows(rows):
        current = latest.get(row.symbol)
        if current is None or row.trade_date > current.trade_date:
            latest[row.symbol] = row
    return {symbol: float(row.market_value) for symbol, row in latest.items()}


_ANALYST_CONSENSUS_FIELDS = frozenset(AnalystConsensus.model_fields)
_INSTITUTIONAL_HOLDER_FIELDS = frozenset(InstitutionalHolderRow.model_fields)
_INSIDER_ACTIVITY_FIELDS = frozenset(InsiderActivityRow.model_fields)
_SECTOR_CLASS_FIELDS = frozenset(SectorClassification.model_fields)


def _latest_analyst_consensus_by_symbol(
    rows: Sequence[dict[str, object]],
) -> dict[str, AnalystConsensus]:
    """This symbol's most recent `analyst_consensus` row from the read window — used by both the
    US `analyst_revision` 多空評分 factor and the US `analyst_revision` 趨勢評分 dimension (shared
    read, not a duplicate fetch)."""
    latest: dict[str, AnalystConsensus] = {}
    for raw in rows:
        try:
            row = AnalystConsensus.model_validate(_project(raw, _ANALYST_CONSENSUS_FIELDS))
        except ValueError:
            continue
        current = latest.get(row.symbol)
        if current is None or row.as_of > current.as_of:
            latest[row.symbol] = row
    return latest


def _institutional_holders_by_symbol(
    rows: Sequence[dict[str, object]],
) -> dict[str, list[InstitutionalHolderRow]]:
    """Every `institutional_holders` row in the read window, grouped by symbol — a snapshot of
    current top holders, not a time series (see `InstitutionalHolderRow`'s own docstring), so
    every row in the window is kept rather than deduplicated to "latest per symbol"."""
    by_symbol: dict[str, list[InstitutionalHolderRow]] = defaultdict(list)
    for raw in rows:
        try:
            row = InstitutionalHolderRow.model_validate(_project(raw, _INSTITUTIONAL_HOLDER_FIELDS))
        except ValueError:
            continue
        by_symbol[row.symbol].append(row)
    return dict(by_symbol)


def _insider_activity_by_symbol(
    rows: Sequence[dict[str, object]],
) -> dict[str, list[InsiderActivityRow]]:
    """Every `insider_activity` row in the read window, grouped by symbol."""
    by_symbol: dict[str, list[InsiderActivityRow]] = defaultdict(list)
    for raw in rows:
        try:
            row = InsiderActivityRow.model_validate(_project(raw, _INSIDER_ACTIVITY_FIELDS))
        except ValueError:
            continue
        by_symbol[row.symbol].append(row)
    return dict(by_symbol)


def _sector_key_by_symbol(rows: Sequence[dict[str, object]]) -> dict[str, str]:
    """This symbol's most recently-ingested `sector_class` slug (`sector_key`) — `sector_class`
    rows have no `as_of` field (a symbol's sector rarely changes), so the last row read for a
    given symbol wins, matching `ParquetStore.read_range`'s own ascending-by-date read order."""
    by_symbol: dict[str, str] = {}
    for raw in rows:
        try:
            row = SectorClassification.model_validate(_project(raw, _SECTOR_CLASS_FIELDS))
        except ValueError:
            continue
        if row.sector_key is not None:
            by_symbol[row.symbol] = row.sector_key
    return by_symbol


def _split_universe_by_symbol_type(
    universe: Sequence[str], market: Market = Market.TW
) -> tuple[list[str], list[str]]:
    """Every symbol's scoring branch is decided purely by its code shape (`classify_symbol_type`
    needs no store lookup) — the one place `job_scoring_batch` chooses which of the two factor
    sets (`StockScoreBreakdown` vs `EtfScoreBreakdown`) a symbol gets. `ETF_LEVERAGED`/
    `ETF_INVERSE` are grouped with plain `ETF` here (all three use the same `EtfScoreBreakdown`
    shape — see that model's own docstring). Every `Market.US` symbol is unconditionally
    `SymbolType.STOCK` (see that function's own docstring) — `etf_symbols` is always empty for
    US."""
    stock_symbols: list[str] = []
    etf_symbols: list[str] = []
    for symbol in universe:
        if classify_symbol_type(symbol, market) is SymbolType.STOCK:
            stock_symbols.append(symbol)
        else:
            etf_symbols.append(symbol)
    return stock_symbols, etf_symbols


def _project(row: dict[str, object], fields: frozenset[str]) -> dict[str, object]:
    """Keep only the model's own fields — `ParquetStore.read_range`'s Hive-partitioned glob read
    resurfaces the partition-key bookkeeping columns (`dataset`/`market`/`date`/`v`) alongside
    the written row data, which the frozen `extra="forbid"` capability models reject outright.
    Mirrors `analytics/fundamentals.py::_project` (duplicated, not imported — that module is a
    layer this one already depends on for other reasons, but the helper itself is trivial enough
    that importing it across would only obscure which model's fields is meant here)."""
    return {key: value for key, value in row.items() if key in fields}


_ETF_NAV_FIELDS = frozenset(EtfNavRow.model_fields)
_ETF_EXPENSE_FIELDS = frozenset(EtfExpenseInfo.model_fields)
_ACTIVE_ETF_HOLDING_FIELDS = frozenset(ActiveEtfHoldingRow.model_fields)
_ACTIVE_ETF_HOLDING_CHANGE_FIELDS = frozenset(ActiveEtfHoldingChangeRow.model_fields)


def _latest_etf_nav_by_symbol(rows: Sequence[dict[str, object]]) -> dict[str, float]:
    """This symbol's most recent published premium/discount %, from whatever `etf_nav` rows
    fell in the read window — `None`-valued rows (TWSE published no estimate that day) are
    dropped, never treated as a 0% deviation."""
    latest: dict[str, EtfNavRow] = {}
    for raw in rows:
        try:
            row = EtfNavRow.model_validate(_project(raw, _ETF_NAV_FIELDS))
        except ValueError:
            continue
        current = latest.get(row.symbol)
        if current is None or row.as_of > current.as_of:
            latest[row.symbol] = row
    return {
        symbol: row.premium_discount_pct
        for symbol, row in latest.items()
        if row.premium_discount_pct is not None
    }


def _latest_etf_expense_ratio_by_symbol(rows: Sequence[dict[str, object]]) -> dict[str, float]:
    """This symbol's most recent SITCA-disclosed approximate TER — SITCA's report only refreshes
    monthly, so `as_of` may be `None` on some upstream rows; a row with `as_of` set always wins
    over one without, and among dated rows the newer one wins."""
    latest: dict[str, EtfExpenseInfo] = {}
    for raw in rows:
        try:
            row = EtfExpenseInfo.model_validate(_project(raw, _ETF_EXPENSE_FIELDS))
        except ValueError:
            continue
        current = latest.get(row.symbol)
        if current is None or (
            row.as_of is not None and (current.as_of is None or row.as_of > current.as_of)
        ):
            latest[row.symbol] = row
    return {
        symbol: row.total_expense_ratio_pct_approx
        for symbol, row in latest.items()
        if row.total_expense_ratio_pct_approx is not None
    }


def _active_etf_symbols(rows: Sequence[dict[str, object]]) -> set[str]:
    """Which ETF symbols had a constituent-holdings snapshot at all in the read window — the only
    ground truth for "is this an active ETF" (`ActiveEtfHoldingRow` is an active-ETF-only FinMind
    capability; a passive/leveraged/inverse ETF is never present here, see
    `_management_activity_factor`'s own docstring)."""
    symbols: set[str] = set()
    for raw in rows:
        try:
            row = ActiveEtfHoldingRow.model_validate(_project(raw, _ACTIVE_ETF_HOLDING_FIELDS))
        except ValueError:
            continue
        symbols.add(row.symbol)
    return symbols


def _holdings_turnover_by_symbol(rows: Sequence[dict[str, object]]) -> dict[str, float]:
    """Each active ETF's holdings-turnover proxy over the read window: the sum of every
    constituent buy/sell share count's magnitude across every row for that ETF — a magnitude
    (how much the portfolio moved), not a net direction, since "management activity" is about
    how actively the fund trades, not which way."""
    turnover: dict[str, float] = defaultdict(float)
    for raw in rows:
        try:
            row = ActiveEtfHoldingChangeRow.model_validate(
                _project(raw, _ACTIVE_ETF_HOLDING_CHANGE_FIELDS)
            )
        except ValueError:
            continue
        turnover[row.symbol] += abs(row.buy) + abs(row.sell)
    return dict(turnover)


def _ma_series_for(bars: Sequence[PriceBar]) -> dict[str, tuple[float | None, ...]]:
    return {f"ma{window}": sma(bars, window).values for window in _MA_WINDOWS}


def _cross_sectional_universe(values: Mapping[str, float | None]) -> list[float]:
    return [value for value in values.values() if value is not None]


def _fundamental_universe(
    metrics_by_symbol: Mapping[str, FundamentalMetrics], attrs: Sequence[str]
) -> dict[str, list[float]]:
    universe: dict[str, list[float]] = {attr: [] for attr in attrs}
    for metrics in metrics_by_symbol.values():
        for attr in attrs:
            value = getattr(metrics, attr)
            if value is not None:
                universe[attr].append(value)
    return universe


@dataclass(frozen=True)
class _PerSymbolSignals:
    """Every symbol's already-assembled raw inputs, before any cross-sectional universe can be
    built from them — the output of pass 1, and the per-symbol lookup pass 2 reads from.

    `analyst_consensus_by_symbol` is US-only (empty dict for a TW pass — `analyst_revision` has
    no TW counterpart, see `UsStockScoreBreakdown`'s own docstring)."""

    bars_by_symbol: dict[str, tuple[PriceBar, ...]]
    ma_series_by_symbol: dict[str, dict[str, tuple[float | None, ...]]]
    price_by_symbol: dict[str, float | None]
    ma50_by_symbol: dict[str, float | None]
    flow_value_by_symbol: dict[str, float | None]
    metrics_by_symbol: dict[str, FundamentalMetrics]
    analyst_consensus_by_symbol: dict[str, AnalystConsensus]


@dataclass(frozen=True)
class _CrossSectionalUniverses:
    """The whole day's comparison universes — the whole reason the entire universe is scored in
    one pass rather than symbol-by-symbol: every percentile/z-score in pass 2 ranks against these,
    so they must be complete (built from every symbol's pass-1 output) before pass 2 can start.

    `eps_trend_delta`/`growth_estimate`/`price_target_upside`/`dollar_volume` are US-only (empty
    list for a TW pass, same reasoning as `_PerSymbolSignals.analyst_consensus_by_symbol`)."""

    momentum: list[float]
    flow: list[float]
    quality: dict[str, list[float]]
    value: dict[str, list[float]]
    eps_trend_delta: list[float]
    growth_estimate: list[float]
    price_target_upside: list[float]
    dollar_volume: list[float]


def _assemble_signals_and_universes(  # noqa: PLR0913 — one keyword-only field per upstream frame
    universe: Sequence[str],
    *,
    bars_by_symbol: dict[str, tuple[PriceBar, ...]],
    flow_by_symbol: dict[str, list[InstitutionalFlowRow]],
    lines_by_symbol: dict[str, list[FinancialStatementLine]],
    market_value_by_symbol: dict[str, float],
    analyst_consensus_by_symbol: dict[str, AnalystConsensus] | None = None,
) -> tuple[_PerSymbolSignals, _CrossSectionalUniverses]:
    """Pass 1 (each symbol's raw momentum, MA series, price/MA50, chip-flow net, and fundamental
    metrics — each computed independently per symbol) plus the cross-sectional universe assembly
    that depends on pass 1's full output."""
    ma_series_by_symbol: dict[str, dict[str, tuple[float | None, ...]]] = {}
    price_by_symbol: dict[str, float | None] = {}
    ma50_by_symbol: dict[str, float | None] = {}
    raw_momentum: dict[str, float | None] = {}
    flow_value_by_symbol: dict[str, float | None] = {}
    metrics_by_symbol: dict[str, FundamentalMetrics] = {}
    consensus_by_symbol = analyst_consensus_by_symbol or {}
    raw_eps_trend_delta: dict[str, float | None] = {}
    raw_growth_estimate: dict[str, float | None] = {}
    raw_price_target_upside: dict[str, float | None] = {}
    raw_dollar_volume: dict[str, float | None] = {}

    for symbol in universe:
        bars = bars_by_symbol.get(symbol, ())
        closes = [bar.close for bar in bars]
        raw_momentum[symbol] = twelve_minus_one_momentum(closes) if closes else None
        raw_dollar_volume[symbol] = average_dollar_volume(closes, [bar.volume for bar in bars])
        ma_series: dict[str, tuple[float | None, ...]] = (
            _ma_series_for(bars) if bars else {f"ma{w}": () for w in _MA_WINDOWS}
        )
        ma_series_by_symbol[symbol] = ma_series
        price_by_symbol[symbol] = closes[-1] if closes else None
        ma50_by_symbol[symbol] = ma_series["ma50"][-1] if ma_series["ma50"] else None

        symbol_flow_rows = [
            row for row in flow_by_symbol.get(symbol, ()) if row.investor in _FLOW_INVESTORS
        ]
        flow_value_by_symbol[symbol] = net_buy_20d_trend(
            symbol_flow_rows, investors=_FLOW_INVESTORS
        )

        lines = lines_by_symbol.get(symbol, [])
        mcap = market_value_by_symbol.get(symbol)
        metrics_by_symbol[symbol] = compute_fundamental_metrics(lines, market_value=mcap)

        consensus = consensus_by_symbol.get(symbol)
        raw_eps_trend_delta[symbol] = eps_trend_delta(consensus) if consensus is not None else None
        raw_growth_estimate[symbol] = (
            consensus.current_year_growth_estimate if consensus is not None else None
        )
        raw_price_target_upside[symbol] = (
            price_target_upside(consensus, price_by_symbol[symbol])
            if consensus is not None
            else None
        )

    signals = _PerSymbolSignals(
        bars_by_symbol=bars_by_symbol,
        ma_series_by_symbol=ma_series_by_symbol,
        price_by_symbol=price_by_symbol,
        ma50_by_symbol=ma50_by_symbol,
        flow_value_by_symbol=flow_value_by_symbol,
        metrics_by_symbol=metrics_by_symbol,
        analyst_consensus_by_symbol=consensus_by_symbol,
    )
    universes = _CrossSectionalUniverses(
        momentum=_cross_sectional_universe(raw_momentum),
        flow=_cross_sectional_universe(flow_value_by_symbol),
        quality=_fundamental_universe(metrics_by_symbol, _QUALITY_ATTRS),
        value=_fundamental_universe(metrics_by_symbol, _VALUE_ATTRS),
        eps_trend_delta=_cross_sectional_universe(raw_eps_trend_delta),
        growth_estimate=_cross_sectional_universe(raw_growth_estimate),
        price_target_upside=_cross_sectional_universe(raw_price_target_upside),
        dollar_volume=_cross_sectional_universe(raw_dollar_volume),
    )
    return signals, universes


@dataclass(frozen=True)
class _EtfSignals:
    """The ETF-only per-symbol raw inputs `compute_etf_factor_breakdown` needs, mirroring
    `_PerSymbolSignals`'s stock-side shape — kept as its own dataclass rather than folded into
    `_PerSymbolSignals` since these fields (NAV/expense-ratio/holdings-turnover) are meaningless
    for a plain stock and would otherwise sit empty on every stock symbol's signals."""

    premium_discount_by_symbol: dict[str, float]
    expense_ratio_by_symbol: dict[str, float]
    active_etf_symbols: set[str]
    holdings_turnover_by_symbol: dict[str, float]


@dataclass(frozen=True)
class _EtfCrossSectionalUniverses:
    """The ETF-only comparison universes — cost and management-activity rank cross-sectionally
    against other ETFs, never against the stock universe (mirrors `_CrossSectionalUniverses`'s
    stock-side shape)."""

    expense_ratio: list[float]
    holdings_turnover: list[float]


def _score_stock_symbol(
    symbol: str, *, signals: _PerSymbolSignals, universes: _CrossSectionalUniverses
) -> StockScoreBreakdown:
    bars = signals.bars_by_symbol.get(symbol, ())
    closes = [bar.close for bar in bars]
    inputs = FactorInputs(
        closes=closes,
        ma_series=signals.ma_series_by_symbol[symbol],
        momentum_universe=universes.momentum,
        flow_value=signals.flow_value_by_symbol[symbol],
        flow_universe=universes.flow,
        price=signals.price_by_symbol[symbol],
        ma50=signals.ma50_by_symbol[symbol],
        fundamentals=signals.metrics_by_symbol[symbol],
        quality_universe=universes.quality,
        value_universe=universes.value,
    )
    return compute_stock_factor_breakdown(inputs)


def _score_us_stock_symbol(
    symbol: str, *, signals: _PerSymbolSignals, universes: _CrossSectionalUniverses
) -> UsStockScoreBreakdown:
    """The `Market.US` counterpart of `_score_stock_symbol` — same price/fundamental inputs, plus
    `analyst_consensus`/`eps_trend_delta_universe` for the `analyst_revision` factor in place of
    `revenue_flow`, `growth_estimate_universe`/`price_target_upside_universe` for the `growth`
    factor, and `volumes`/`dollar_volume_universe` for the seventh `liquidity` factor."""
    bars = signals.bars_by_symbol.get(symbol, ())
    closes = [bar.close for bar in bars]
    inputs = FactorInputs(
        closes=closes,
        ma_series=signals.ma_series_by_symbol[symbol],
        momentum_universe=universes.momentum,
        flow_value=signals.flow_value_by_symbol[symbol],
        flow_universe=universes.flow,
        price=signals.price_by_symbol[symbol],
        ma50=signals.ma50_by_symbol[symbol],
        fundamentals=signals.metrics_by_symbol[symbol],
        quality_universe=universes.quality,
        value_universe=universes.value,
        analyst_consensus=signals.analyst_consensus_by_symbol.get(symbol),
        eps_trend_delta_universe=universes.eps_trend_delta,
        growth_estimate_universe=universes.growth_estimate,
        price_target_upside_universe=universes.price_target_upside,
        volumes=[bar.volume for bar in bars],
        dollar_volume_universe=universes.dollar_volume,
    )
    return compute_us_stock_factor_breakdown(inputs)


def _score_etf_symbol(
    symbol: str,
    *,
    signals: _PerSymbolSignals,
    universes: _CrossSectionalUniverses,
    etf_signals: _EtfSignals,
    etf_universes: _EtfCrossSectionalUniverses,
) -> EtfScoreBreakdown:
    bars = signals.bars_by_symbol.get(symbol, ())
    closes = [bar.close for bar in bars]
    inputs = EtfFactorInputs(
        closes=closes,
        momentum_universe=universes.momentum,
        flow_value=signals.flow_value_by_symbol[symbol],
        flow_universe=universes.flow,
        price=signals.price_by_symbol[symbol],
        ma50=signals.ma50_by_symbol[symbol],
        premium_discount_pct=etf_signals.premium_discount_by_symbol.get(symbol),
        expense_ratio_pct=etf_signals.expense_ratio_by_symbol.get(symbol),
        expense_ratio_universe=etf_universes.expense_ratio,
        is_active_etf=symbol in etf_signals.active_etf_symbols,
        holdings_turnover=etf_signals.holdings_turnover_by_symbol.get(symbol),
        holdings_turnover_universe=etf_universes.holdings_turnover,
    )
    return compute_etf_factor_breakdown(inputs)


async def _score_and_persist_universe(  # noqa: PLR0913 — one keyword-only field per pass-2 input
    session_factory: async_sessionmaker[AsyncSession],
    *,
    stock_symbols: Sequence[str],
    etf_symbols: Sequence[str],
    market: Market,
    run_date: date_,
    signals: _PerSymbolSignals,
    universes: _CrossSectionalUniverses,
    etf_signals: _EtfSignals,
    etf_universes: _EtfCrossSectionalUniverses,
) -> int:
    """Pass 2: build each symbol's breakdown (stock or ETF branch, by symbol type) against the
    now-complete cross-sectional universes, blend both horizon profiles, and upsert every row in
    one transaction. Returns the number of rows written (symbols x 2 profiles)."""
    rows_written = 0
    async with session_factory() as session:
        for symbol in stock_symbols:
            breakdown: StockScoreBreakdown | UsStockScoreBreakdown = (
                _score_us_stock_symbol(symbol, signals=signals, universes=universes)
                if market is Market.US
                else _score_stock_symbol(symbol, signals=signals, universes=universes)
            )
            for profile in (ScoreProfile.SWING, ScoreProfile.POSITION):
                result: ScoreResult = build_score_result(
                    symbol=symbol,
                    market=market,
                    as_of=run_date,
                    profile=profile,
                    symbol_type=SymbolType.STOCK,
                    breakdown=breakdown,
                )
                await upsert_score(session, result)
                rows_written += 1
        for symbol in etf_symbols:
            etf_breakdown = _score_etf_symbol(
                symbol,
                signals=signals,
                universes=universes,
                etf_signals=etf_signals,
                etf_universes=etf_universes,
            )
            for profile in (ScoreProfile.SWING, ScoreProfile.POSITION):
                result = build_score_result(
                    symbol=symbol,
                    market=market,
                    as_of=run_date,
                    profile=profile,
                    symbol_type=classify_symbol_type(symbol, market),
                    breakdown=etf_breakdown,
                )
                await upsert_score(session, result)
                rows_written += 1
        await session.commit()
    return rows_written


async def job_scoring_batch(run_date: date_, ctx: IngestContext) -> JobRun:
    """Compute and cache the composite score for every symbol the day's ingested price/flow/
    fundamental data covers, for both `ScoreProfile.SWING` and `ScoreProfile.POSITION`. Leader-
    and calendar-gated exactly like `job_screener_flags`: a non-trading day is a journaled skip;
    a non-leader pass writes nothing. Idempotent — a re-run for the same date upserts the same
    `(symbol, market, profile, as_of)` rows rather than accumulating history. Uses the
    extrapolating check since `run_date` is almost always "today", which is always beyond
    `ctx.calendar`'s known range until today's own whole-market pulls have landed — see
    `TradingDayCalendar.is_trading_day_extrapolating`'s own docstring."""
    if not _calendar_for(ctx).is_trading_day_extrapolating(run_date):
        return journal_run(
            STOCK_SCORE_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            STOCK_SCORE_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )
    if ctx.session_factory is None:
        raise RuntimeError("scoring batch requires IngestContext.session_factory to be wired")

    market = ctx.market
    market_str = market.value
    price_window = _trailing_trading_days(ctx, run_date, _PRICE_LOOKBACK_DAYS)
    flow_window = _trailing_trading_days(ctx, run_date, _FLOW_LOOKBACK_DAYS)
    financials_window = _trailing_trading_days(ctx, run_date, _FINANCIALS_LOOKBACK_DAYS)
    market_value_window = _trailing_trading_days(ctx, run_date, _MARKET_VALUE_LOOKBACK_DAYS)
    etf_nav_window = _trailing_trading_days(ctx, run_date, _ETF_NAV_LOOKBACK_DAYS)
    etf_expense_ratio_window = _trailing_trading_days(
        ctx, run_date, _ETF_EXPENSE_RATIO_LOOKBACK_DAYS
    )
    active_etf_holding_window = _trailing_trading_days(
        ctx, run_date, _ACTIVE_ETF_HOLDING_LOOKBACK_DAYS
    )
    holdings_turnover_window = _trailing_trading_days(
        ctx, run_date, _HOLDINGS_TURNOVER_LOOKBACK_DAYS
    )
    us_analyst_data_window = _trailing_trading_days(ctx, run_date, _US_ANALYST_DATA_LOOKBACK_DAYS)

    financials_dataset, balance_sheet_dataset, cash_flow_dataset = _financials_datasets(market)
    price_frame = ctx.store.read_range_frame(
        _price_eod_dataset(market), market_str, price_window, columns=_PRICE_FRAME_COLUMNS
    )
    # US has no institutional-flow dataset ingested at all (`ingest/us_jobs.py` covers only
    # price/fundamentals) — reading the TW-only dataset name for `market_str="US"` always finds
    # no partitions and returns an empty frame, the same graceful "nothing ingested yet" path
    # `ParquetStore.read_range_frame` already takes for any dataset with no written partitions;
    # `revenue_flow`/`liquidity_flow`/chip-flow-dependent factors then degrade to unavailable
    # exactly as `analytics/score.py`'s own graceful-degradation contract already guarantees.
    flow_frame = ctx.store.read_range_frame(
        INSTITUTIONAL_FLOW_DATASET, market_str, flow_window, columns=_FLOW_FRAME_COLUMNS
    )
    statement_rows = (
        ctx.store.read_range(financials_dataset, market_str, financials_window)
        + ctx.store.read_range(balance_sheet_dataset, market_str, financials_window)
        + ctx.store.read_range(cash_flow_dataset, market_str, financials_window)
    )
    # market_value/etf_nav/etf_expense_ratio/active_etf_holding have no US-ingested counterpart
    # either — same empty-frame graceful degradation as institutional-flow above; value_guard's
    # market-cap-dependent sub-metrics (EV/EBITDA, FCF yield, P/E, P/B) degrade to unavailable for
    # US, and every ETF-only factor is moot since `_split_universe_by_symbol_type` never assigns
    # a US symbol to the ETF branch (see that function's own docstring).
    market_value_rows = ctx.store.read_range(MARKET_VALUE_DATASET, market_str, market_value_window)
    etf_nav_rows = ctx.store.read_range(ETF_NAV_DATASET, market_str, etf_nav_window)
    etf_expense_ratio_rows = ctx.store.read_range(
        ETF_EXPENSE_RATIO_DATASET, market_str, etf_expense_ratio_window
    )
    active_etf_holding_rows = ctx.store.read_range(
        ACTIVE_ETF_HOLDING_DATASET, market_str, active_etf_holding_window
    )
    active_etf_holding_change_rows = ctx.store.read_range(
        ACTIVE_ETF_HOLDING_CHANGE_DATASET, market_str, holdings_turnover_window
    )
    # US-only: analyst_consensus for the `analyst_revision` factor (no TW branch — this dataset is
    # never written for Market.TW, see `US_ANALYST_CONSENSUS_DATASET`'s own comment).
    us_analyst_consensus_rows = (
        ctx.store.read_range(US_ANALYST_CONSENSUS_DATASET, market_str, us_analyst_data_window)
        if market is Market.US
        else []
    )

    bars_by_symbol = _bars_by_symbol(price_frame, market)
    flow_by_symbol = _flow_rows_by_symbol(flow_frame, market)
    lines_by_symbol = _statement_lines_by_symbol(statement_rows)
    market_value_by_symbol = _latest_market_value_by_symbol(market_value_rows)
    premium_discount_by_symbol = _latest_etf_nav_by_symbol(etf_nav_rows)
    expense_ratio_by_symbol = _latest_etf_expense_ratio_by_symbol(etf_expense_ratio_rows)
    active_etf_symbols = _active_etf_symbols(active_etf_holding_rows)
    holdings_turnover_by_symbol = _holdings_turnover_by_symbol(active_etf_holding_change_rows)
    analyst_consensus_by_symbol = _latest_analyst_consensus_by_symbol(us_analyst_consensus_rows)

    raw_universe = (
        bars_by_symbol.keys()
        | flow_by_symbol.keys()
        | lines_by_symbol.keys()
        | premium_discount_by_symbol.keys()
        | expense_ratio_by_symbol.keys()
        | active_etf_symbols
        | holdings_turnover_by_symbol.keys()
        | analyst_consensus_by_symbol.keys()
    )
    active_roster = _active_roster_symbols(ctx, market_str)
    universe = sorted(raw_universe if active_roster is None else raw_universe & active_roster)
    if not universe:
        return journal_run(
            STOCK_SCORE_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SUCCESS,
            detail="no upstream price/flow/fundamental/ETF history in the read window — "
            "nothing to score",
        )

    stock_symbols, etf_symbols = _split_universe_by_symbol_type(universe, market)

    signals, universes = _assemble_signals_and_universes(
        universe,
        bars_by_symbol=bars_by_symbol,
        flow_by_symbol=flow_by_symbol,
        lines_by_symbol=lines_by_symbol,
        market_value_by_symbol=market_value_by_symbol,
        analyst_consensus_by_symbol=analyst_consensus_by_symbol,
    )
    etf_signals = _EtfSignals(
        premium_discount_by_symbol=premium_discount_by_symbol,
        expense_ratio_by_symbol=expense_ratio_by_symbol,
        active_etf_symbols=active_etf_symbols,
        holdings_turnover_by_symbol=holdings_turnover_by_symbol,
    )
    etf_universes = _EtfCrossSectionalUniverses(
        expense_ratio=list(expense_ratio_by_symbol.values()),
        holdings_turnover=[
            value
            for symbol, value in holdings_turnover_by_symbol.items()
            if symbol in active_etf_symbols
        ],
    )
    rows_written = await _score_and_persist_universe(
        ctx.session_factory,
        stock_symbols=stock_symbols,
        etf_symbols=etf_symbols,
        market=market,
        run_date=run_date,
        signals=signals,
        universes=universes,
        etf_signals=etf_signals,
        etf_universes=etf_universes,
    )

    logger.info(
        "scoring_batch_computed",
        market=market_str,
        run_date=run_date.isoformat(),
        symbols=len(universe),
        stock_symbols=len(stock_symbols),
        etf_symbols=len(etf_symbols),
        rows_written=rows_written,
    )
    return journal_run(
        STOCK_SCORE_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=f"scored {len(stock_symbols)} stocks + {len(etf_symbols)} ETFs x 2 profiles "
        f"over {market_str} price/flow/fundamental/ETF history",
    )


# ---------------------------------------------------------------------------------------------
# 趨勢評分 (trend score) batch
# ---------------------------------------------------------------------------------------------


async def _margin_short_rows_by_symbol(
    ctx: IngestContext, market: Market, margin_window: Sequence[date_]
) -> dict[str, list[MarginShortRow]]:
    """Every symbol's margin/short balance rows over `margin_window`, fetched directly through
    `ctx.finmind.fetch_whole_market` (see `MARGIN_SHORT_DATASET`'s own module-level comment for
    why this batch calls the adapter itself rather than reading back a `ParquetStore` partition
    `run_whole_market_job` would have written — no `WholeMarketJobSpec`/cron entry exists for
    `Capability.MARGIN_SHORT` yet, only the adapter-level whole-market capability). One call per
    day in the window, same cadence discipline `run_whole_market_job` itself follows (one call
    per dataset per day, no retry loop) — a day whose fetch fails is logged and skipped, never
    retried in a tight loop; the day's rows are simply absent from the returned mapping."""
    by_symbol: dict[str, list[MarginShortRow]] = defaultdict(list)
    if market is not Market.TW:
        # `ctx.finmind.fetch_whole_market` has no `market` parameter at all — FinMind's own
        # `MARGIN_SHORT` dataset is TW-only, with no US equivalent ingested anywhere. Calling it
        # unconditionally here would silently fetch TW margin data and mislabel it under a US
        # symbol universe. Returns empty — `margin_credit_trend_score` degrades to unavailable
        # for every non-TW market exactly like every other genuinely-missing dimension.
        return {}
    for run_date in margin_window:
        result = await ctx.finmind.fetch_whole_market(
            Capability.MARGIN_SHORT, tls_policy=ctx.tls_policy, start_date=run_date.isoformat()
        )
        if result.health.status not in (HealthStatus.OK, HealthStatus.STALE):
            logger.warning(
                "trend_score_margin_short_fetch_failed",
                market=market.value,
                run_date=run_date.isoformat(),
                detail=result.health.detail,
            )
            continue
        raw_rows = result.payload.get("rows")
        if not isinstance(raw_rows, list):
            continue
        for raw_row in cast("list[dict[str, object]]", raw_rows):
            try:
                row = MarginShortRow.model_validate(raw_row)
            except ValueError:
                continue
            by_symbol[row.symbol].append(row)
    return dict(by_symbol)


_US_REFERENCE_INDEX_SYMBOL = "SPY"  # `ingest/us_jobs.py::US_REFERENCE_INDEX_SYMBOL`, duplicated
# as a local read-key constant — same decoupling convention as `PRICE_EOD_DATASET` etc. above.


def _reference_index_closes(price_frame: pd.DataFrame, market: Market) -> tuple[float, ...]:
    """The broad-market reference index's own daily closes from the SAME whole-market
    `PRICE_EOD_DATASET`/`US_PRICE_EOD_DATASET` frame `_bars_by_symbol` reads — read back
    separately because `scoreable_symbol_mask` (the four-digit-or-`00`-prefixed equity/ETF
    filter) deliberately excludes the non-numeric `TAIEX` aggregate label for TW (see that
    mask's own docstring); SPY is a real, `is_scoreable_symbol`-unfiltered ticker for US (that
    mask is a no-op there), but is read back the same way here for symmetry with the TW path and
    because `_bars_by_symbol`'s per-symbol grouping is a different shape than the flat closes
    list this dimension needs. TAIEX (TW) or SPY (US, `ingest/us_jobs.py::job_us_price_eod`
    always fetches it alongside the discovered universe) — ascending by trade date."""
    index_symbol = _US_REFERENCE_INDEX_SYMBOL if market is Market.US else _TAIEX_SYMBOL
    projected = cast("pd.DataFrame", price_frame.loc[:, ["symbol", "trade_date", "close"]])
    index_rows = cast(
        "pd.DataFrame",
        projected.loc[projected["symbol"] == index_symbol].sort_values("trade_date"),
    )
    closes: list[float] = []
    for close in cast("list[object]", index_rows["close"].tolist()):
        close_f = _as_float(close)
        if close_f is not None:
            closes.append(close_f)
    return tuple(closes)


def _industry_by_symbol(ctx: IngestContext, market_str: str) -> dict[str, str]:
    """The latest `STOCK_INFO_JOB` roster's `symbol -> industry_category` map — the same roster
    read `_active_roster_symbols` already performs, duplicated here (not refactored to share)
    because that function returns a `frozenset` of symbols for a membership filter, a genuinely
    different shape than this `dict` a caller does a per-symbol lookup against."""
    partition_dates = ctx.store.list_partition_dates(STOCK_INFO_DATASET, market_str)
    if not partition_dates:
        return {}
    roster_date = max(partition_dates)
    raw_roster = ctx.store.read_partition(STOCK_INFO_DATASET, market_str, roster_date)
    field_names = set(StockInfoRow.model_fields)
    roster = [
        StockInfoRow.model_validate({k: v for k, v in row.items() if k in field_names})
        for row in raw_roster
    ]
    return {row.symbol: row.industry_category for row in roster if row.industry_category}


def _sector_rotation_ranks_by_industry(
    flow_by_symbol: Mapping[str, list[InstitutionalFlowRow]],
    industry_by_symbol: Mapping[str, str],
) -> dict[str, list[int]]:
    """Duplicates `tools/market_tools.py::GetSectorRotationTool`'s own daily-industry-rank
    arithmetic (sum `buy - sell` across every investor category, per industry, per day; rank
    industries by that sum, most-inflow-first) rather than importing it — that tool lives in
    `tools/`, a HIGHER layer than `analytics/` (backend/CLAUDE.md §3's one-way dependency rule),
    so `analytics/` may never import from it. This is the "pull the small computation DOWN into
    `analytics/`" resolution the task brief called for, not the "duplicate across `tools/`
    modules" convention `tools/theme_tools.py` describes for peer-to-peer tool duplication (that
    convention is about `tools/` modules not importing each other, not about crossing the
    `tools/` -> `analytics/` boundary).

    Returns `{industry -> [rank_day1, rank_day2, ...]}` in chronological order (oldest first),
    over whatever trading days `flow_by_symbol`'s rows actually cover — the same trailing window
    `job_trend_score_batch` already read for `chip_flow_trend_score`, reused here rather than a
    second fetch."""
    net_by_industry_by_date: dict[date_, dict[str, float]] = defaultdict(dict)
    for symbol, rows in flow_by_symbol.items():
        industry = industry_by_symbol.get(symbol)
        if industry is None:
            continue
        for row in rows:
            bucket = net_by_industry_by_date[row.trade_date]
            bucket[industry] = bucket.get(industry, 0.0) + (row.buy - row.sell)
    ranks_by_industry: dict[str, list[int]] = defaultdict(list)
    for trade_date in sorted(net_by_industry_by_date):
        net_by_industry = net_by_industry_by_date[trade_date]
        ranked = sorted(net_by_industry.items(), key=lambda item: (-item[1], item[0]))
        for rank, (industry, _net) in enumerate(ranked, start=1):
            ranks_by_industry[industry].append(rank)
    return dict(ranks_by_industry)


def _sector_momentum_returns_by_key(
    bars_by_symbol: Mapping[str, tuple[PriceBar, ...]],
    sector_key_by_symbol: Mapping[str, str],
) -> dict[str, float]:
    """The day's US sector-relative-momentum comparison universe: each sector's average trailing
    ~3-month (`_SECTOR_MOMENTUM_LOOKBACK_DAYS`) return across every symbol resolved to that sector
    — the real cross-sectional input `sector_momentum_trend_score` ranks against (Moskowitz &
    Grinblatt industry-momentum convention). A symbol with fewer than
    `_SECTOR_MOMENTUM_LOOKBACK_DAYS` bars contributes nothing to its sector's average, never a
    fabricated partial-window return."""
    returns_by_sector: dict[str, list[float]] = defaultdict(list)
    for symbol, bars in bars_by_symbol.items():
        sector_key = sector_key_by_symbol.get(symbol)
        if sector_key is None or len(bars) < _SECTOR_MOMENTUM_LOOKBACK_DAYS:
            continue
        window = bars[-_SECTOR_MOMENTUM_LOOKBACK_DAYS:]
        start_close = window[0].close
        if start_close <= 0:
            continue
        symbol_return = (window[-1].close - start_close) / start_close
        returns_by_sector[sector_key].append(symbol_return)
    return {
        sector_key: statistics.fmean(returns)
        for sector_key, returns in returns_by_sector.items()
        if returns
    }


_NON_TRADING_TREND_CAVEAT = (
    "Descriptive trend-strength read across technical/chip/credit/concentration/rotation/"
    "co-movement evidence only — not a buy, sell, hold, or price recommendation; computed at "
    "last post-close and may not reflect intraday moves. 分點集中度 (broker concentration) is "
    "computed on demand per symbol at request time (Sponsor-tier, per-symbol-fetch-only data — "
    "see `job_trend_score_batch`'s own docstring), not by this nightly batch."
)

_BROKER_CONCENTRATION_BATCH_UNAVAILABLE = TrendDimension(
    name="broker_concentration",
    value=None,
    description="broker_concentration unavailable",
    unavailable_reason="分點 (chip_broker) has no whole-market request form (per-symbol/"
    "per-broker only, Sponsor-tier) — computed on demand by the get_trend_score tool for the "
    "one requested symbol, not by this nightly whole-universe batch.",
)


def _align_closes(
    symbol_closes: Sequence[float], taiex_closes: Sequence[float]
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """`taiex_correlation_score` requires same-length aligned series — this batch's own
    `symbol_closes`/`taiex_closes` come from independent per-symbol/whole-market reads over the
    same trailing window, so they align by taking the shortest common trailing slice of each
    (both are already ascending-by-date), rather than a real date-keyed join. Good enough for a
    descriptive co-movement read; a caller needing exact per-date alignment (unlikely to differ,
    since both come from the same trading-day window) would need a date-keyed join instead."""
    length = min(len(symbol_closes), len(taiex_closes))
    if length == 0:
        return (), ()
    return tuple(symbol_closes[-length:]), tuple(taiex_closes[-length:])


def _build_tw_trend_result(  # noqa: PLR0913 — one keyword-only field per dimension's own raw input
    symbol: str,
    *,
    run_date: date_,
    bars: Sequence[PriceBar],
    flow_value: float | None,
    flow_universe: Sequence[float],
    margin_rows: Sequence[MarginShortRow],
    price_change_pct: float | None,
    industry_ranks: Sequence[int],
    symbol_closes: Sequence[float],
    taiex_closes: Sequence[float],
) -> TrendScoreResult:
    """Blends the FIVE whole-market-ingested-data TW dimensions this batch can compute
    (`technical`/`chip_flow`/`margin_credit`/`sector_rotation`/`taiex_correlation`) plus a
    disclosed-unavailable placeholder for `broker_concentration` — see the module's
    `job_trend_score_batch` docstring for why that sixth dimension is deliberately never
    batch-computed (always disclosed unavailable here, on the same "never fabricate, always
    disclose" terms every other genuinely missing dimension gets), with the tool layer blending
    it in on demand at request time instead."""
    dimensions: list[TrendDimension] = [
        technical_trend_score(bars),
        chip_flow_trend_score(flow_value, flow_universe),
        margin_credit_trend_score(margin_rows, price_change_pct=price_change_pct),
        _BROKER_CONCENTRATION_BATCH_UNAVAILABLE,
        sector_rotation_trend_score(industry_ranks),
        taiex_correlation_score(symbol_closes, taiex_closes),
    ]
    composite, unavailable = blend_trend_composite(dimensions)
    dimension_results = tuple(
        TrendDimensionResult(
            name=d.name,
            value=d.value,
            description=d.description,
            unavailable_reason=d.unavailable_reason,
        )
        for d in dimensions
    )
    return TrendScoreResult(
        symbol=symbol,
        market=Market.TW,
        as_of=run_date,
        composite=composite,
        dimensions=dimension_results,
        unavailable_dimensions=unavailable,
        non_trading_caveat=_NON_TRADING_TREND_CAVEAT,
    )


_NON_TRADING_US_TREND_CAVEAT = (
    "Descriptive trend-strength read across technical/institutional-ownership/insider-sentiment/"
    "analyst-revision/sector-momentum/S&P 500 co-movement evidence only — not a buy, sell, hold, "
    "or price recommendation; computed at last post-close and may not reflect intraday moves. "
    "None of TW's chip/margin/broker/sector-rotation dimensions apply to a US symbol — every "
    "dimension here is a US-native replacement grounded in yfinance-sourced data."
)


def _build_us_trend_result(  # noqa: PLR0913 — one keyword-only field per dimension's own raw input
    symbol: str,
    *,
    run_date: date_,
    bars: Sequence[PriceBar],
    holders: Sequence[InstitutionalHolderRow],
    insider_activity: Sequence[InsiderActivityRow],
    consensus: AnalystConsensus | None,
    eps_trend_delta_universe: Sequence[float],
    sector_key: str | None,
    sector_returns_by_key: Mapping[str, float],
    symbol_closes: Sequence[float],
    reference_index_closes: Sequence[float],
) -> TrendScoreResult:
    """The `Market.US` counterpart of `_build_tw_trend_result` — six genuinely US-native
    dimensions, zero TW-shaped placeholders (see `analytics/trend_score.py`'s US dimension
    functions for the professional-factor grounding of each)."""
    dimensions: list[TrendDimension] = [
        technical_trend_score(bars),
        institutional_ownership_trend_score(holders),
        insider_sentiment_trend_score(insider_activity),
        analyst_revision_trend_score(consensus, eps_trend_delta_universe),
        sector_momentum_trend_score(sector_key, sector_returns_by_key),
        taiex_correlation_score(symbol_closes, reference_index_closes),
    ]
    composite, unavailable = blend_trend_composite(dimensions, weights=US_DIMENSION_WEIGHTS)
    dimension_results = tuple(
        TrendDimensionResult(
            name=d.name,
            value=d.value,
            description=d.description,
            unavailable_reason=d.unavailable_reason,
        )
        for d in dimensions
    )
    return TrendScoreResult(
        symbol=symbol,
        market=Market.US,
        as_of=run_date,
        composite=composite,
        dimensions=dimension_results,
        unavailable_dimensions=unavailable,
        non_trading_caveat=_NON_TRADING_US_TREND_CAVEAT,
    )


async def job_trend_score_batch(run_date: date_, ctx: IngestContext) -> JobRun:
    """Compute and cache 5 of the 6 趨勢評分 (trend score) dimensions for every TW stock/ETF
    symbol that day's ingested price/flow/margin-short/roster data covers, and upsert every row
    into the relational `trend_scores` history table (`analytics/trend_score_store.py`).
    Leader- and calendar-gated exactly like `job_scoring_batch`.

    **Why only 5 of 6 dimensions, and which one is missing:** `broker_concentration_trend_score`
    needs `Capability.CHIP_BROKER` (分點) rows, and that dataset has NO documented whole-market
    request form — `ingest/jobs.py`'s own `STUBBED_DATASETS` comment confirms it: the upstream
    endpoint "only accepts a request keyed by *either* a stock id *or* a broker id, never
    neither" (per-symbol/per-broker only, Sponsor-tier). Computing it for the whole universe
    nightly would mean one live per-symbol fetch per stock — exactly the "per-symbol trap" this
    codebase's whole-market-ingest discipline exists to forbid (backend/CLAUDE.md §4's "No
    full-market scan" rule: ingest is the *only* whole-market path, and even ingest itself has no
    legitimate whole-market form for this one dataset). The clean fix, and the one this batch
    takes: score the other five dimensions here nightly (all backed by genuinely whole-market-
    ingested `price_eod`/`institutional_flow`/`margin_short`/`stock_info` data), and leave
    `broker_concentration` a disclosed-unavailable placeholder in every row this batch writes —
    the tool layer's own `get_trend_score` `run()` computes that sixth dimension on demand for
    just the ONE requested symbol (a single-symbol Sponsor-tier fetch, not a batch concern) and
    re-blends the full six-dimension composite at request time, never persisting that on-demand
    value back into this table. This mirrors no other exact precedent in this codebase (no other
    card mixes a batch-computed value with an on-demand one), so the split is a `job_trend_score_
    batch`-and-`get_trend_score`-owned decision, documented here and at the tool layer, not a
    project-wide pattern.

    **Sector rotation's rank-computation logic** is duplicated from
    `tools/market_tools.py::GetSectorRotationTool` into `_sector_rotation_ranks_by_industry`
    above, rather than imported — `tools/` sits ABOVE `analytics/` in the one-way dependency
    order (backend/CLAUDE.md §3), so `analytics/` may never import it; pulling the small
    arithmetic down into `analytics/` (not leaving it only in `tools/`) is the correct-direction
    fix, not the peer-to-peer "duplicate rather than cross-import" convention `tools/
    theme_tools.py` documents for tool-to-tool duplication (a different scenario — that
    convention never licenses an upward import in the first place).

    **Margin/short (`Capability.MARGIN_SHORT`) IS whole-market-capable** at the adapter level
    (`sources/finmind/chip_institutional.py`'s own `_DatasetConfig` sets `whole_market_ok=True`)
    even though no `WholeMarketJobSpec`/cron entry exists for it yet in `ingest/jobs.py` (its
    `STUBBED_DATASETS` entry documents only the missing *scheduler wiring*, not a missing
    adapter capability) — this batch calls `ctx.finmind.fetch_whole_market` for it directly
    (`_margin_short_rows_by_symbol`) rather than waiting on that separate scheduler-wiring phase.

    Scores BOTH stock and ETF symbols with the same five dimensions (no ETF-specific branching,
    per the task brief: the five factors here are mostly stock-agnostic)."""
    if not _calendar_for(ctx).is_trading_day_extrapolating(run_date):
        return journal_run(
            TREND_SCORE_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="non-trading day (calendar-gated)",
        )
    if not ctx.leader.is_leader():
        return journal_run(
            TREND_SCORE_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )
    if ctx.session_factory is None:
        raise RuntimeError("trend score batch requires IngestContext.session_factory to be wired")

    market = ctx.market
    market_str = market.value
    price_window = _trailing_trading_days(ctx, run_date, _PRICE_LOOKBACK_DAYS)

    price_frame = ctx.store.read_range_frame(
        _price_eod_dataset(market), market_str, price_window, columns=_PRICE_FRAME_COLUMNS
    )
    bars_by_symbol = _bars_by_symbol(price_frame, market)
    reference_index_closes = _reference_index_closes(price_frame, market)

    if market is Market.US:
        return await _run_us_trend_score_batch(
            ctx,
            run_date,
            session_factory=ctx.session_factory,
            bars_by_symbol=bars_by_symbol,
            reference_index_closes=reference_index_closes,
        )

    flow_window = _trailing_trading_days(ctx, run_date, _FLOW_LOOKBACK_DAYS)
    margin_window = _trailing_trading_days(ctx, run_date, _MARGIN_SHORT_LOOKBACK_DAYS)

    flow_frame = ctx.store.read_range_frame(
        INSTITUTIONAL_FLOW_DATASET, market_str, flow_window, columns=_FLOW_FRAME_COLUMNS
    )
    flow_by_symbol = _flow_rows_by_symbol(flow_frame, market)
    margin_by_symbol = await _margin_short_rows_by_symbol(ctx, market, margin_window)
    industry_by_symbol = _industry_by_symbol(ctx, market_str)
    industry_ranks_by_industry = _sector_rotation_ranks_by_industry(
        flow_by_symbol, industry_by_symbol
    )

    raw_universe = bars_by_symbol.keys() | flow_by_symbol.keys() | margin_by_symbol.keys()
    active_roster = _active_roster_symbols(ctx, market_str)
    universe = sorted(raw_universe if active_roster is None else raw_universe & active_roster)
    if not universe:
        return journal_run(
            TREND_SCORE_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SUCCESS,
            detail="no upstream price/flow/margin-short history in the read window — "
            "nothing to score",
        )

    # Cross-sectional chip-flow universe — same `net_buy_20d_trend` sub-signal
    # `job_scoring_batch` already ranks, rebuilt here since `chip_flow_trend_score` needs its own
    # day's comparison universe and this batch does not share `job_scoring_batch`'s in-memory
    # pass (see the module docstring's "a second bounded read is acceptable" note).
    flow_value_by_symbol: dict[str, float | None] = {}
    for symbol in universe:
        symbol_flow_rows = [
            row for row in flow_by_symbol.get(symbol, ()) if row.investor in _FLOW_INVESTORS
        ]
        flow_value_by_symbol[symbol] = net_buy_20d_trend(
            symbol_flow_rows, investors=_FLOW_INVESTORS
        )
    flow_universe = _cross_sectional_universe(flow_value_by_symbol)

    rows_written = 0
    async with ctx.session_factory() as session:
        for symbol in universe:
            bars = bars_by_symbol.get(symbol, ())
            closes = [bar.close for bar in bars]
            price_change_pct = (
                (closes[-1] - closes[-2]) / closes[-2]
                if len(closes) >= _PRICE_CHANGE_MIN_BARS and closes[-2] != 0
                else None
            )
            industry = industry_by_symbol.get(symbol)
            industry_ranks = industry_ranks_by_industry.get(industry, []) if industry else []
            symbol_closes, aligned_index_closes = _align_closes(closes, reference_index_closes)
            result = _build_tw_trend_result(
                symbol,
                run_date=run_date,
                bars=bars,
                flow_value=flow_value_by_symbol.get(symbol),
                flow_universe=flow_universe,
                margin_rows=margin_by_symbol.get(symbol, []),
                price_change_pct=price_change_pct,
                industry_ranks=industry_ranks,
                symbol_closes=symbol_closes,
                taiex_closes=aligned_index_closes,
            )
            await upsert_trend_score(session, result)
            rows_written += 1
        await session.commit()

    logger.info(
        "trend_score_batch_computed",
        market=market_str,
        run_date=run_date.isoformat(),
        symbols=len(universe),
        rows_written=rows_written,
    )
    return journal_run(
        TREND_SCORE_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=f"scored {len(universe)} symbols (5 of 6 dimensions; broker_concentration "
        f"deferred to on-demand tool-layer compute) over {market_str} price/flow/margin-short "
        "history",
    )


async def _run_us_trend_score_batch(
    ctx: IngestContext,
    run_date: date_,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    bars_by_symbol: Mapping[str, tuple[PriceBar, ...]],
    reference_index_closes: Sequence[float],
) -> JobRun:
    """The `Market.US` branch of `job_trend_score_batch` — reads the four US-native datasets
    (`us_institutional_holders`/`us_insider_activity`/`us_analyst_consensus`/`us_sector_class`,
    all written by `ingest/us_jobs.py::job_us_fundamentals`'s extended fan-out) instead of TW's
    institutional-flow/margin-short/roster datasets, and calls `_build_us_trend_result` per
    symbol. Split out of `job_trend_score_batch` to keep that function's TW/US branches each
    readably linear rather than one function threading both paths through shared local state."""
    market_str = Market.US.value
    analyst_data_window = _trailing_trading_days(ctx, run_date, _US_ANALYST_DATA_LOOKBACK_DAYS)

    holder_rows = ctx.store.read_range(
        US_INSTITUTIONAL_HOLDERS_DATASET, market_str, analyst_data_window
    )
    insider_rows = ctx.store.read_range(
        US_INSIDER_ACTIVITY_DATASET, market_str, analyst_data_window
    )
    consensus_rows = ctx.store.read_range(
        US_ANALYST_CONSENSUS_DATASET, market_str, analyst_data_window
    )
    sector_rows = ctx.store.read_range(US_SECTOR_CLASS_DATASET, market_str, analyst_data_window)

    holders_by_symbol = _institutional_holders_by_symbol(holder_rows)
    insider_by_symbol = _insider_activity_by_symbol(insider_rows)
    consensus_by_symbol = _latest_analyst_consensus_by_symbol(consensus_rows)
    sector_key_by_symbol = _sector_key_by_symbol(sector_rows)
    sector_returns_by_key = _sector_momentum_returns_by_key(bars_by_symbol, sector_key_by_symbol)

    raw_universe = (
        bars_by_symbol.keys()
        | holders_by_symbol.keys()
        | insider_by_symbol.keys()
        | consensus_by_symbol.keys()
    )
    active_roster = _active_roster_symbols(ctx, market_str)
    universe = sorted(raw_universe if active_roster is None else raw_universe & active_roster)
    if not universe:
        return journal_run(
            TREND_SCORE_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SUCCESS,
            detail="no upstream price/institutional-holders/insider-activity/analyst-consensus "
            "history in the read window — nothing to score",
        )

    eps_trend_delta_universe = [
        delta
        for symbol in universe
        if (consensus := consensus_by_symbol.get(symbol)) is not None
        and (delta := eps_trend_delta(consensus)) is not None
    ]

    rows_written = 0
    async with session_factory() as session:
        for symbol in universe:
            bars = bars_by_symbol.get(symbol, ())
            closes = [bar.close for bar in bars]
            symbol_closes, aligned_index_closes = _align_closes(closes, reference_index_closes)
            result = _build_us_trend_result(
                symbol,
                run_date=run_date,
                bars=bars,
                holders=holders_by_symbol.get(symbol, []),
                insider_activity=insider_by_symbol.get(symbol, []),
                consensus=consensus_by_symbol.get(symbol),
                eps_trend_delta_universe=eps_trend_delta_universe,
                sector_key=sector_key_by_symbol.get(symbol),
                sector_returns_by_key=sector_returns_by_key,
                symbol_closes=symbol_closes,
                reference_index_closes=aligned_index_closes,
            )
            await upsert_trend_score(session, result)
            rows_written += 1
        await session.commit()

    logger.info(
        "trend_score_batch_computed",
        market=market_str,
        run_date=run_date.isoformat(),
        symbols=len(universe),
        rows_written=rows_written,
    )
    return journal_run(
        TREND_SCORE_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=f"scored {len(universe)} US symbols across 6 US-native dimensions "
        "(technical/institutional_ownership/insider_sentiment/analyst_revision/"
        "sector_momentum/taiex_correlation)",
    )


__all__ = [
    "ACTIVE_ETF_HOLDING_CHANGE_DATASET",
    "ACTIVE_ETF_HOLDING_DATASET",
    "ETF_EXPENSE_RATIO_DATASET",
    "ETF_NAV_DATASET",
    "INSTITUTIONAL_FLOW_DATASET",
    "MARGIN_SHORT_DATASET",
    "PRICE_EOD_DATASET",
    "STOCK_SCORE_DATASET",
    "TREND_SCORE_DATASET",
    "job_scoring_batch",
    "job_trend_score_batch",
]
