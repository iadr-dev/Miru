"""The daily multi-factor scoring batch (`job_scoring_batch`) and its per-symbol scorers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date as date_
from typing import cast

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.fundamentals import (
    MARKET_VALUE_DATASET,
    FundamentalMetrics,
    compute_fundamental_metrics,
)
from analytics.indicators import PriceBar
from analytics.models import (
    EtfScoreBreakdown,
    ScoreProfile,
    ScoreResult,
    StockScoreBreakdown,
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
from analytics.scoring_batch.datasets import (
    ACTIVE_ETF_HOLDING_CHANGE_DATASET,
    ACTIVE_ETF_HOLDING_DATASET,
    ETF_EXPENSE_RATIO_DATASET,
    ETF_NAV_DATASET,
    INSTITUTIONAL_FLOW_DATASET,
    STOCK_SCORE_DATASET,
    US_ANALYST_CONSENSUS_DATASET,
)
from analytics.scoring_batch.datasets import (
    ACTIVE_ETF_HOLDING_LOOKBACK_DAYS as _ACTIVE_ETF_HOLDING_LOOKBACK_DAYS,
)
from analytics.scoring_batch.datasets import (
    ETF_EXPENSE_RATIO_LOOKBACK_DAYS as _ETF_EXPENSE_RATIO_LOOKBACK_DAYS,
)
from analytics.scoring_batch.datasets import ETF_NAV_LOOKBACK_DAYS as _ETF_NAV_LOOKBACK_DAYS
from analytics.scoring_batch.datasets import FINANCIALS_LOOKBACK_DAYS as _FINANCIALS_LOOKBACK_DAYS
from analytics.scoring_batch.datasets import FLOW_INVESTORS as _FLOW_INVESTORS
from analytics.scoring_batch.datasets import FLOW_LOOKBACK_DAYS as _FLOW_LOOKBACK_DAYS
from analytics.scoring_batch.datasets import (
    HOLDINGS_TURNOVER_LOOKBACK_DAYS as _HOLDINGS_TURNOVER_LOOKBACK_DAYS,
)
from analytics.scoring_batch.datasets import MA_WINDOWS as _MA_WINDOWS
from analytics.scoring_batch.datasets import (
    MARKET_VALUE_LOOKBACK_DAYS as _MARKET_VALUE_LOOKBACK_DAYS,
)
from analytics.scoring_batch.datasets import PRICE_LOOKBACK_DAYS as _PRICE_LOOKBACK_DAYS
from analytics.scoring_batch.datasets import QUALITY_ATTRS as _QUALITY_ATTRS
from analytics.scoring_batch.datasets import (
    US_ANALYST_DATA_LOOKBACK_DAYS as _US_ANALYST_DATA_LOOKBACK_DAYS,
)
from analytics.scoring_batch.datasets import VALUE_ATTRS as _VALUE_ATTRS
from analytics.scoring_batch.datasets import financials_datasets as _financials_datasets
from analytics.scoring_batch.datasets import price_eod_dataset as _price_eod_dataset
from analytics.scoring_batch.inputs import FLOW_FRAME_COLUMNS as _FLOW_FRAME_COLUMNS
from analytics.scoring_batch.inputs import PRICE_FRAME_COLUMNS as _PRICE_FRAME_COLUMNS
from analytics.scoring_batch.inputs import STATEMENT_COLUMNS as _STATEMENT_COLUMNS
from analytics.scoring_batch.inputs import STATEMENT_KEY_COLUMNS as _STATEMENT_KEY_COLUMNS
from analytics.scoring_batch.inputs import TW_STATEMENT_COLUMNS as _TW_STATEMENT_COLUMNS
from analytics.scoring_batch.inputs import US_STATEMENT_ORDER
from analytics.scoring_batch.inputs import active_etf_symbols as _active_etf_symbols
from analytics.scoring_batch.inputs import active_roster_symbols as _active_roster_symbols
from analytics.scoring_batch.inputs import bars_by_symbol as _bars_by_symbol
from analytics.scoring_batch.inputs import calendar_for as _calendar_for
from analytics.scoring_batch.inputs import cross_sectional_universe as _cross_sectional_universe
from analytics.scoring_batch.inputs import flow_rows_by_symbol as _flow_rows_by_symbol
from analytics.scoring_batch.inputs import fundamental_universe as _fundamental_universe
from analytics.scoring_batch.inputs import (
    holdings_turnover_by_symbol as _holdings_turnover_by_symbol,
)
from analytics.scoring_batch.inputs import (
    latest_analyst_consensus_by_symbol as _latest_analyst_consensus_by_symbol,
)
from analytics.scoring_batch.inputs import (
    latest_etf_expense_ratio_by_symbol as _latest_etf_expense_ratio_by_symbol,
)
from analytics.scoring_batch.inputs import latest_etf_nav_by_symbol as _latest_etf_nav_by_symbol
from analytics.scoring_batch.inputs import (
    latest_market_value_by_symbol as _latest_market_value_by_symbol,
)
from analytics.scoring_batch.inputs import lazy_statement_lines as _lazy_statement_lines
from analytics.scoring_batch.inputs import ma_series_for as _ma_series_for
from analytics.scoring_batch.inputs import (
    split_universe_by_symbol_type as _split_universe_by_symbol_type,
)
from analytics.scoring_batch.inputs import trailing_trading_days as _trailing_trading_days
from analytics.scoring_store import upsert_score
from analytics.symbol_universe import SymbolType, classify_symbol_type, scoreable_symbol_sql
from ingest.context import IngestContext
from ingest.jobs.source_chain import fetch_whole_market_chained
from ingest.journal import JobRun, RunStatus, journal_run
from schemas.capabilities import (
    AnalystConsensus,
    FinancialStatementLine,
    InstitutionalFlowRow,
    MarginShortRow,
)
from schemas.enums import Capability, Market
from schemas.source import HealthStatus

logger = structlog.get_logger(__name__)


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


def _ma_series_or_empty(bars: Sequence[PriceBar]) -> dict[str, tuple[float | None, ...]]:
    return _ma_series_for(bars) if bars else {f"ma{w}": () for w in _MA_WINDOWS}


def _scored_flow_trend(rows: Sequence[InstitutionalFlowRow]) -> float | None:
    scored = [row for row in rows if row.investor in _FLOW_INVESTORS]
    return net_buy_20d_trend(scored, investors=_FLOW_INVESTORS)


def _consensus_signals(
    consensus: AnalystConsensus | None, price: float | None
) -> tuple[float | None, float | None, float | None]:
    """The US-only (eps-trend delta, growth estimate, price-target upside) raw signals."""
    if consensus is None:
        return None, None, None
    return (
        eps_trend_delta(consensus),
        consensus.current_year_growth_estimate,
        price_target_upside(consensus, price),
    )


def _assemble_signals_and_universes(  # noqa: PLR0913 — one keyword-only field per upstream frame
    universe: Sequence[str],
    *,
    bars_by_symbol: dict[str, tuple[PriceBar, ...]],
    flow_by_symbol: dict[str, list[InstitutionalFlowRow]],
    lines_by_symbol: Mapping[str, list[FinancialStatementLine]],
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
        ma_series = _ma_series_or_empty(bars)
        ma_series_by_symbol[symbol] = ma_series
        price_by_symbol[symbol] = closes[-1] if closes else None
        ma50_by_symbol[symbol] = ma_series["ma50"][-1] if ma_series["ma50"] else None

        flow_value_by_symbol[symbol] = _scored_flow_trend(flow_by_symbol.get(symbol, ()))

        lines = lines_by_symbol.get(symbol, [])
        mcap = market_value_by_symbol.get(symbol)
        metrics_by_symbol[symbol] = compute_fundamental_metrics(lines, market_value=mcap)

        (
            raw_eps_trend_delta[symbol],
            raw_growth_estimate[symbol],
            raw_price_target_upside[symbol],
        ) = _consensus_signals(consensus_by_symbol.get(symbol), price_by_symbol[symbol])

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


def _load_statement_lines(
    ctx: IngestContext,
    market: Market,
    statement_datasets: Sequence[str],
    window: Sequence[date_],
) -> Mapping[str, list[FinancialStatementLine]]:
    """Each symbol's financial-statement lines for the window.

    Both markets keep the rows as columnar frames and turn them into `FinancialStatementLine`s one
    symbol at a time (`LazyStatementLines`), not all at once. Taiwan: 1.48M rows in the window; the
    eager form took the process to 3.06 GB on its own. US: the newest copy of each line (the
    statements are a full weekly re-pull: 14.4M raw rows, 4M distinct), about 4M rows, in a defined
    order (quarterly before annual for the same period)."""
    market_str = market.value
    if market is Market.US:
        return _lazy_statement_lines(
            [
                ctx.store.read_range_latest_by_key_frame(
                    dataset,
                    market_str,
                    window,
                    columns=_STATEMENT_COLUMNS,
                    key_columns=_STATEMENT_KEY_COLUMNS,
                    order_by=US_STATEMENT_ORDER,
                )
                for dataset in statement_datasets
            ]
        )
    return _lazy_statement_lines(
        [
            ctx.store.read_range_frame(dataset, market_str, window, columns=_TW_STATEMENT_COLUMNS)
            for dataset in statement_datasets
        ]
    )


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
    if not ctx.store.has_rows(_price_eod_dataset(ctx.market), ctx.market.value, run_date):
        # Without that day's prices the trailing window ends on an older day, and the result would
        # be stored under `run_date` as if it were fresh (and never recomputed once the prices
        # arrive, since the day would then look done). Skip instead; a later run fills it in.
        return journal_run(
            STOCK_SCORE_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=f"no {ctx.market.value} price data for {run_date} yet; not using older prices",
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
        _price_eod_dataset(market),
        market_str,
        price_window,
        columns=_PRICE_FRAME_COLUMNS,
        where=scoreable_symbol_sql(market),  # warrants (~90% of rows) never leave DuckDB
    )
    # US has no institutional-flow dataset ingested at all (`ingest/us_jobs.py` covers only
    # price/fundamentals) — reading the TW-only dataset name for `market_str="US"` always finds
    # no partitions and returns an empty frame, the same graceful "nothing ingested yet" path
    # `ParquetStore.read_range_frame` already takes for any dataset with no written partitions;
    # `revenue_flow`/`liquidity_flow`/chip-flow-dependent factors then degrade to unavailable
    # exactly as `analytics/score.py`'s own graceful-degradation contract already guarantees.
    flow_frame = ctx.store.read_range_frame(
        INSTITUTIONAL_FLOW_DATASET,
        market_str,
        flow_window,
        columns=_FLOW_FRAME_COLUMNS,
        where=scoreable_symbol_sql(market),
    )
    statement_datasets = (financials_dataset, balance_sheet_dataset, cash_flow_dataset)
    lines_by_symbol = _load_statement_lines(ctx, market, statement_datasets, financials_window)
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


async def margin_short_rows_by_symbol(
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
        result = await fetch_whole_market_chained(ctx, Capability.MARGIN_SHORT, run_date, market)
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
