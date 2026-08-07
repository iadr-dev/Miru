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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date as date_

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
from analytics.models import ScoreProfile, ScoreResult
from analytics.score import (
    FactorInputs,
    build_score_result,
    compute_factor_breakdown,
    net_buy_20d_trend,
    twelve_minus_one_momentum,
)
from analytics.scoring_store import upsert_score
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run
from schemas.capabilities import FinancialStatementLine, InstitutionalFlowRow, MarketValueRow
from schemas.enums import Market

logger = structlog.get_logger(__name__)

STOCK_SCORE_DATASET = "stock_scores"

# Upstream derived datasets this job reads back. Local read-key constants (matching the
# decoupling convention `analytics/fundamentals.py` and `analytics/screener/batch.py` already
# use) rather than importing the ingest job specs, so this compute layer does not bind to the
# scheduler's wiring.
PRICE_EOD_DATASET = "TaiwanStockPrice"
INSTITUTIONAL_FLOW_DATASET = "TaiwanStockInstitutionalInvestorsBuySell"

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


def _trade_date_key(row: dict[str, object]) -> str:
    value = row.get("trade_date")
    return value if isinstance(value, str) else str(value)


def _bars_by_symbol(price_rows: Sequence[dict[str, object]]) -> dict[str, tuple[PriceBar, ...]]:
    """Group the raw price rows by symbol and build each an ascending-by-date `PriceBar` series.
    A row with no usable close is dropped rather than faked (matches the ingest mapper and
    `screener/batch.py`'s own `_bars_for_symbol`)."""
    by_symbol: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in price_rows:
        symbol = row.get("symbol")
        if isinstance(symbol, str) and symbol:
            by_symbol[symbol].append(row)

    bars_by_symbol: dict[str, tuple[PriceBar, ...]] = {}
    for symbol, rows in by_symbol.items():
        ordered = sorted(rows, key=_trade_date_key)
        bars: list[PriceBar] = []
        for row in ordered:
            close = _as_float(row.get("close"))
            high = _as_float(row.get("high"))
            low = _as_float(row.get("low"))
            if close is None or close <= 0 or high is None or low is None:
                continue
            bars.append(
                PriceBar(
                    high=high,
                    low=low,
                    close=close,
                    volume=_as_int(row.get("volume")),
                    open=_as_float(row.get("open")),
                    turnover=_as_float(row.get("turnover")),
                )
            )
        bars_by_symbol[symbol] = tuple(bars)
    return bars_by_symbol


def _flow_rows_by_symbol(
    flow_rows: Sequence[dict[str, object]], market: Market
) -> dict[str, list[InstitutionalFlowRow]]:
    by_symbol: dict[str, list[InstitutionalFlowRow]] = defaultdict(list)
    for row in flow_rows:
        symbol = row.get("symbol")
        investor = row.get("investor")
        trade_date = row.get("trade_date")
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
                buy=_as_int(row.get("buy")),
                sell=_as_int(row.get("sell")),
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


async def _score_and_persist_universe(  # noqa: PLR0913 — one keyword-only field per pass-2 input
    session_factory: async_sessionmaker[AsyncSession],
    *,
    universe: Sequence[str],
    market: Market,
    run_date: date_,
    signals: _PerSymbolSignals,
    universes: _CrossSectionalUniverses,
) -> int:
    """Pass 2: build each symbol's breakdown against the now-complete cross-sectional universes,
    blend both horizon profiles, and upsert every row in one transaction. Returns the number of
    rows written (symbols x 2 profiles)."""
    rows_written = 0
    async with session_factory() as session:
        for symbol in universe:
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
            breakdown = compute_factor_breakdown(inputs)
            for profile in (ScoreProfile.SWING, ScoreProfile.POSITION):
                result: ScoreResult = build_score_result(
                    symbol=symbol,
                    market=market,
                    as_of=run_date,
                    profile=profile,
                    breakdown=breakdown,
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
    `(symbol, market, profile, as_of)` rows rather than accumulating history."""
    if not ctx.calendar.is_trading_day(run_date):
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

    price_rows = ctx.store.read_range(PRICE_EOD_DATASET, market_str, price_window)
    flow_rows = ctx.store.read_range(INSTITUTIONAL_FLOW_DATASET, market_str, flow_window)
    statement_rows = (
        ctx.store.read_range(FINANCIALS_DATASET, market_str, financials_window)
        + ctx.store.read_range(BALANCE_SHEET_DATASET, market_str, financials_window)
        + ctx.store.read_range(CASH_FLOW_DATASET, market_str, financials_window)
    )
    market_value_rows = ctx.store.read_range(MARKET_VALUE_DATASET, market_str, market_value_window)

    bars_by_symbol = _bars_by_symbol(price_rows)
    flow_by_symbol = _flow_rows_by_symbol(flow_rows, market)
    lines_by_symbol = _statement_lines_by_symbol(statement_rows)
    market_value_by_symbol = _latest_market_value_by_symbol(market_value_rows)

    universe = sorted(bars_by_symbol.keys() | flow_by_symbol.keys() | lines_by_symbol.keys())
    if not universe:
        return journal_run(
            STOCK_SCORE_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SUCCESS,
            detail="no upstream price/flow/fundamental history in the read window — nothing "
            "to score",
        )

    signals, universes = _assemble_signals_and_universes(
        universe,
        bars_by_symbol=bars_by_symbol,
        flow_by_symbol=flow_by_symbol,
        lines_by_symbol=lines_by_symbol,
        market_value_by_symbol=market_value_by_symbol,
    )
    rows_written = await _score_and_persist_universe(
        ctx.session_factory,
        universe=universe,
        market=market,
        run_date=run_date,
        signals=signals,
        universes=universes,
    )

    logger.info(
        "scoring_batch_computed",
        market=market_str,
        run_date=run_date.isoformat(),
        symbols=len(universe),
        rows_written=rows_written,
    )
    return journal_run(
        STOCK_SCORE_DATASET,
        run_date,
        rows_written=rows_written,
        status=RunStatus.SUCCESS,
        detail=f"scored {len(universe)} symbols x 2 profiles over {market_str} "
        "price/flow/fundamental history",
    )


__all__ = [
    "INSTITUTIONAL_FLOW_DATASET",
    "PRICE_EOD_DATASET",
    "STOCK_SCORE_DATASET",
    "job_scoring_batch",
]
