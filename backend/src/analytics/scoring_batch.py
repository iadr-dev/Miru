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
from analytics.models import EtfScoreBreakdown, ScoreProfile, ScoreResult, StockScoreBreakdown
from analytics.score import (
    EtfFactorInputs,
    FactorInputs,
    build_score_result,
    compute_etf_factor_breakdown,
    compute_stock_factor_breakdown,
    net_buy_20d_trend,
    twelve_minus_one_momentum,
)
from analytics.scoring_store import upsert_score
from analytics.symbol_universe import SymbolType, classify_symbol_type, scoreable_symbol_mask
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run
from schemas.capabilities import (
    ActiveEtfHoldingChangeRow,
    ActiveEtfHoldingRow,
    EtfExpenseInfo,
    EtfNavRow,
    FinancialStatementLine,
    InstitutionalFlowRow,
    MarketValueRow,
    StockInfoRow,
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
# listed" is ever checked (`tools/market_tools.py::GetSymbolInfoTool`'s own docstring) — the
# leaderboard would show a bare code with no name forever, not just for one stale day.
STOCK_INFO_DATASET = "TaiwanStockInfo"

# FinMind investor-category labels the revenue/flow factor's chip sub-signal sums.
_FLOW_INVESTORS = ("Foreign_Investor", "Investment_Trust")

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


def _trailing_trading_days(ctx: IngestContext, run_date: date_, count: int) -> tuple[date_, ...]:
    """The `count` trading days ending at (and including) `run_date`, via the trading-day
    calendar so the window skips weekends/holidays rather than hand-rolling date math — same
    helper shape `screener/batch.py` uses."""
    start = ctx.calendar.shift_business_days(run_date, -(count - 1))
    return ctx.calendar.business_days_between(start, run_date)


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


def _bars_by_symbol(price_frame: pd.DataFrame) -> dict[str, tuple[PriceBar, ...]]:
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
    # `PriceBar` construction at all.
    scoreable = scoreable_symbol_mask(cast("pd.Series", projected["symbol"]))
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


def _split_universe_by_symbol_type(universe: Sequence[str]) -> tuple[list[str], list[str]]:
    """Every symbol's scoring branch is decided purely by its code shape (`classify_symbol_type`
    needs no store lookup) — the one place `job_scoring_batch` chooses which of the two factor
    sets (`StockScoreBreakdown` vs `EtfScoreBreakdown`) a symbol gets. `ETF_LEVERAGED`/
    `ETF_INVERSE` are grouped with plain `ETF` here (all three use the same `EtfScoreBreakdown`
    shape — see that model's own docstring)."""
    stock_symbols: list[str] = []
    etf_symbols: list[str] = []
    for symbol in universe:
        if classify_symbol_type(symbol) is SymbolType.STOCK:
            stock_symbols.append(symbol)
        else:
            etf_symbols.append(symbol)
    return stock_symbols, etf_symbols


def _latest_etf_nav_by_symbol(rows: Sequence[dict[str, object]]) -> dict[str, float]:
    """This symbol's most recent published premium/discount %, from whatever `etf_nav` rows
    fell in the read window — `None`-valued rows (TWSE published no estimate that day) are
    dropped, never treated as a 0% deviation."""
    latest: dict[str, EtfNavRow] = {}
    for raw in rows:
        try:
            row = EtfNavRow.model_validate(raw)
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
            row = EtfExpenseInfo.model_validate(raw)
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
            row = ActiveEtfHoldingRow.model_validate(raw)
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
            row = ActiveEtfHoldingChangeRow.model_validate(raw)
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
    built from them — the output of pass 1, and the per-symbol lookup pass 2 reads from."""

    bars_by_symbol: dict[str, tuple[PriceBar, ...]]
    ma_series_by_symbol: dict[str, dict[str, tuple[float | None, ...]]]
    price_by_symbol: dict[str, float | None]
    ma50_by_symbol: dict[str, float | None]
    flow_value_by_symbol: dict[str, float | None]
    metrics_by_symbol: dict[str, FundamentalMetrics]


@dataclass(frozen=True)
class _CrossSectionalUniverses:
    """The whole day's comparison universes — the whole reason the entire universe is scored in
    one pass rather than symbol-by-symbol: every percentile/z-score in pass 2 ranks against these,
    so they must be complete (built from every symbol's pass-1 output) before pass 2 can start."""

    momentum: list[float]
    flow: list[float]
    quality: dict[str, list[float]]
    value: dict[str, list[float]]


def _assemble_signals_and_universes(
    universe: Sequence[str],
    *,
    bars_by_symbol: dict[str, tuple[PriceBar, ...]],
    flow_by_symbol: dict[str, list[InstitutionalFlowRow]],
    lines_by_symbol: dict[str, list[FinancialStatementLine]],
    market_value_by_symbol: dict[str, float],
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

    for symbol in universe:
        bars = bars_by_symbol.get(symbol, ())
        closes = [bar.close for bar in bars]
        raw_momentum[symbol] = twelve_minus_one_momentum(closes) if closes else None
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

    signals = _PerSymbolSignals(
        bars_by_symbol=bars_by_symbol,
        ma_series_by_symbol=ma_series_by_symbol,
        price_by_symbol=price_by_symbol,
        ma50_by_symbol=ma50_by_symbol,
        flow_value_by_symbol=flow_value_by_symbol,
        metrics_by_symbol=metrics_by_symbol,
    )
    universes = _CrossSectionalUniverses(
        momentum=_cross_sectional_universe(raw_momentum),
        flow=_cross_sectional_universe(flow_value_by_symbol),
        quality=_fundamental_universe(metrics_by_symbol, _QUALITY_ATTRS),
        value=_fundamental_universe(metrics_by_symbol, _VALUE_ATTRS),
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
            breakdown = _score_stock_symbol(symbol, signals=signals, universes=universes)
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
                    symbol_type=classify_symbol_type(symbol),
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
    if not ctx.calendar.is_trading_day_extrapolating(run_date):
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

    price_frame = ctx.store.read_range_frame(
        PRICE_EOD_DATASET, market_str, price_window, columns=_PRICE_FRAME_COLUMNS
    )
    flow_frame = ctx.store.read_range_frame(
        INSTITUTIONAL_FLOW_DATASET, market_str, flow_window, columns=_FLOW_FRAME_COLUMNS
    )
    statement_rows = (
        ctx.store.read_range(FINANCIALS_DATASET, market_str, financials_window)
        + ctx.store.read_range(BALANCE_SHEET_DATASET, market_str, financials_window)
        + ctx.store.read_range(CASH_FLOW_DATASET, market_str, financials_window)
    )
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

    bars_by_symbol = _bars_by_symbol(price_frame)
    flow_by_symbol = _flow_rows_by_symbol(flow_frame, market)
    lines_by_symbol = _statement_lines_by_symbol(statement_rows)
    market_value_by_symbol = _latest_market_value_by_symbol(market_value_rows)
    premium_discount_by_symbol = _latest_etf_nav_by_symbol(etf_nav_rows)
    expense_ratio_by_symbol = _latest_etf_expense_ratio_by_symbol(etf_expense_ratio_rows)
    active_etf_symbols = _active_etf_symbols(active_etf_holding_rows)
    holdings_turnover_by_symbol = _holdings_turnover_by_symbol(active_etf_holding_change_rows)

    raw_universe = (
        bars_by_symbol.keys()
        | flow_by_symbol.keys()
        | lines_by_symbol.keys()
        | premium_discount_by_symbol.keys()
        | expense_ratio_by_symbol.keys()
        | active_etf_symbols
        | holdings_turnover_by_symbol.keys()
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

    stock_symbols, etf_symbols = _split_universe_by_symbol_type(universe)

    signals, universes = _assemble_signals_and_universes(
        universe,
        bars_by_symbol=bars_by_symbol,
        flow_by_symbol=flow_by_symbol,
        lines_by_symbol=lines_by_symbol,
        market_value_by_symbol=market_value_by_symbol,
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


__all__ = [
    "ACTIVE_ETF_HOLDING_CHANGE_DATASET",
    "ACTIVE_ETF_HOLDING_DATASET",
    "ETF_EXPENSE_RATIO_DATASET",
    "ETF_NAV_DATASET",
    "INSTITUTIONAL_FLOW_DATASET",
    "PRICE_EOD_DATASET",
    "STOCK_SCORE_DATASET",
    "job_scoring_batch",
]
