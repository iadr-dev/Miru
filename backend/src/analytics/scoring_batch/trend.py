"""The daily trend-score batch (`job_trend_score_batch`) for TW and US."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date as date_
from typing import cast

import pandas as pd
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.coerce import as_float
from analytics.indicators import PriceBar
from analytics.models import (
    TrendDimensionResult,
    TrendScoreResult,
)
from analytics.score import (
    eps_trend_delta,
    net_buy_20d_trend,
)
from analytics.scoring_batch.datasets import FLOW_INVESTORS as _FLOW_INVESTORS
from analytics.scoring_batch.datasets import FLOW_LOOKBACK_DAYS as _FLOW_LOOKBACK_DAYS
from analytics.scoring_batch.datasets import (
    INSTITUTIONAL_FLOW_DATASET,
    STOCK_INFO_DATASET,
    TREND_SCORE_DATASET,
    US_ANALYST_CONSENSUS_DATASET,
    US_INSIDER_ACTIVITY_DATASET,
    US_INSTITUTIONAL_HOLDERS_DATASET,
    US_SECTOR_CLASS_DATASET,
)
from analytics.scoring_batch.datasets import (
    MARGIN_SHORT_LOOKBACK_DAYS as _MARGIN_SHORT_LOOKBACK_DAYS,
)
from analytics.scoring_batch.datasets import PRICE_CHANGE_MIN_BARS as _PRICE_CHANGE_MIN_BARS
from analytics.scoring_batch.datasets import PRICE_LOOKBACK_DAYS as _PRICE_LOOKBACK_DAYS
from analytics.scoring_batch.datasets import (
    SECTOR_MOMENTUM_LOOKBACK_DAYS as _SECTOR_MOMENTUM_LOOKBACK_DAYS,
)
from analytics.scoring_batch.datasets import TAIEX_SYMBOL as _TAIEX_SYMBOL
from analytics.scoring_batch.datasets import (
    US_ANALYST_DATA_LOOKBACK_DAYS as _US_ANALYST_DATA_LOOKBACK_DAYS,
)
from analytics.scoring_batch.datasets import price_eod_dataset as _price_eod_dataset
from analytics.scoring_batch.inputs import FLOW_FRAME_COLUMNS as _FLOW_FRAME_COLUMNS
from analytics.scoring_batch.inputs import PRICE_FRAME_COLUMNS as _PRICE_FRAME_COLUMNS
from analytics.scoring_batch.inputs import active_roster_symbols as _active_roster_symbols
from analytics.scoring_batch.inputs import bars_by_symbol as _bars_by_symbol
from analytics.scoring_batch.inputs import calendar_for as _calendar_for
from analytics.scoring_batch.inputs import cross_sectional_universe as _cross_sectional_universe
from analytics.scoring_batch.inputs import flow_rows_by_symbol as _flow_rows_by_symbol
from analytics.scoring_batch.inputs import insider_activity_by_symbol as _insider_activity_by_symbol
from analytics.scoring_batch.inputs import (
    institutional_holders_by_symbol as _institutional_holders_by_symbol,
)
from analytics.scoring_batch.inputs import (
    latest_analyst_consensus_by_symbol as _latest_analyst_consensus_by_symbol,
)
from analytics.scoring_batch.inputs import sector_key_by_symbol as _sector_key_by_symbol
from analytics.scoring_batch.inputs import trailing_trading_days as _trailing_trading_days
from analytics.scoring_batch.scoring import (
    margin_short_rows_by_symbol as _margin_short_rows_by_symbol,
)
from analytics.symbol_universe import scoreable_symbol_sql
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
    AnalystConsensus,
    InsiderActivityRow,
    InstitutionalFlowRow,
    InstitutionalHolderRow,
    MarginShortRow,
    StockInfoRow,
)
from schemas.enums import Market

logger = structlog.get_logger(__name__)


_US_REFERENCE_INDEX_SYMBOL = "SPY"  # `ingest/us_jobs.py::US_REFERENCE_INDEX_SYMBOL`, duplicated


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
        close_f = as_float(close)
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
    """Duplicates `tools/market/sector_rotation.py::GetSectorRotationTool`'s own daily-industry-rank
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
    `tools/market/sector_rotation.py::GetSectorRotationTool` into
    `_sector_rotation_ranks_by_industry`
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
    if not ctx.store.has_rows(_price_eod_dataset(ctx.market), ctx.market.value, run_date):
        # Without that day's prices the trailing window ends on an older day, and the result would
        # be stored under `run_date` as if it were fresh (and never recomputed once the prices
        # arrive, since the day would then look done). Skip instead; a later run fills it in.
        return journal_run(
            TREND_SCORE_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=f"no {ctx.market.value} price data for {run_date} yet; not using older prices",
        )
    if ctx.session_factory is None:
        raise RuntimeError("trend score batch requires IngestContext.session_factory to be wired")

    market = ctx.market
    market_str = market.value
    price_window = _trailing_trading_days(ctx, run_date, _PRICE_LOOKBACK_DAYS)

    price_frame = ctx.store.read_range_frame(
        _price_eod_dataset(market),
        market_str,
        price_window,
        columns=_PRICE_FRAME_COLUMNS,
        # Warrants (~90% of rows) never leave DuckDB; the TAIEX row is kept: the reference-index
        # dimension reads it from this same frame although it is not a scoreable symbol.
        where=scoreable_symbol_sql(market, keep=(_TAIEX_SYMBOL,)),
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
