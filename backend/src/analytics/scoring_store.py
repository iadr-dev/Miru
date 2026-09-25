"""The relational read/write seam over `stock_scores` — the cached multi-factor composite-score
table the daily batch job (`analytics/scoring_batch.py`) writes and both the detail-page and
leaderboard tools read.

Mirrors `analytics/themes/store.py`'s shape: the caller owns the session/transaction boundary
(this module never opens or commits its own session), and every read reconstructs a real
`ScoreResult`/`StockScoreBreakdown`/`EtfScoreBreakdown`/`FactorScore` domain object — never a raw
dict — so callers above this seam never touch the ORM row shape directly.

**Why relational, not Parquet** (unlike `screener/store.py`'s `screener_flags`, which explicitly
chose Parquet): a score is a small, frequently point-looked-up row that is fully replaced once a
day, not a genuinely date-partitioned whole-market series a caller reads a range of — the same
reasoning that put the theme/tier tables in the relational store rather than Parquet. Unlike theme
data, a score row does carry a date (`as_of`), because the value itself is a new number every
trading day; but the read pattern (one row per symbol, or a sorted top-N slice for one day) is a
plain indexed relational query, not a Hive-partitioned range scan.

**Symbol-type-aware factor columns.** `StockScoreRow` carries BOTH the five stock factor columns
AND the five ETF factor columns (all nullable) — a row for a stock leaves the ETF columns null
and vice versa, discriminated by `symbol_type`. This is a wider table than either breakdown alone
needs, but keeps one flat, index-free-read table rather than two separate tables with duplicated
`(symbol, market, profile, as_of)` keys and duplicated leaderboard-query logic.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from datetime import date as date_
from typing import Literal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.models import (
    EtfScoreBreakdown,
    FactorScore,
    ScoreBand,
    ScoreProfile,
    ScoreResult,
    StockScoreBreakdown,
    UsStockScoreBreakdown,
)
from analytics.symbol_universe import SymbolType
from db.models import StockScoreRow
from schemas.enums import Market

LeaderboardDirection = Literal["bullish", "bearish"]
LeaderboardUniverse = Literal["stock", "etf"]

# `SymbolType.ETF`/`ETF_LEVERAGED`/`ETF_INVERSE` all share `EtfScoreBreakdown` — a caller asking
# for "the ETF leaderboard" means all three, not just plain passive ETFs.
_ETF_SYMBOL_TYPES: tuple[str, ...] = (
    SymbolType.ETF.value,
    SymbolType.ETF_LEVERAGED.value,
    SymbolType.ETF_INVERSE.value,
)

# The five factor names, in the fixed order each breakdown model declares them — used to walk
# the flat per-factor column triplets on `StockScoreRow` without repeating the name five times.
_STOCK_FACTOR_NAMES: tuple[str, ...] = (
    "momentum",
    "trend_quality",
    "value_guard",
    "quality",
    "revenue_flow",
)
# `analyst_revision` replaces `revenue_flow` for `Market.US` rows — see `UsStockScoreBreakdown`'s
# own docstring for why the US-market breakdown needs its own factor-name tuple/column set rather
# than reusing `_STOCK_FACTOR_NAMES`.
_US_STOCK_FACTOR_NAMES: tuple[str, ...] = (
    "momentum",
    "trend_quality",
    "value_guard",
    "quality",
    "analyst_revision",
    "growth",
    "liquidity",
)
_ETF_FACTOR_NAMES: tuple[str, ...] = (
    "momentum",
    "premium_discount",
    "cost",
    "management_activity",
    "liquidity_flow",
)


def _factor_score(row: StockScoreRow, name: str) -> FactorScore:
    percentile = getattr(row, f"{name}_percentile")
    description = getattr(row, f"{name}_description")
    unavailable_reason = getattr(row, f"{name}_unavailable_reason")
    return FactorScore(
        name=name,
        percentile=percentile,
        description=description,
        unavailable_reason=unavailable_reason,
    )


def _row_to_result(row: StockScoreRow) -> ScoreResult:
    symbol_type = SymbolType(row.symbol_type)
    market = Market(row.market)
    breakdown: StockScoreBreakdown | UsStockScoreBreakdown | EtfScoreBreakdown
    if symbol_type is not SymbolType.STOCK:
        breakdown = EtfScoreBreakdown(
            **{name: _factor_score(row, name) for name in _ETF_FACTOR_NAMES},
        )
    elif market is Market.US:
        breakdown = UsStockScoreBreakdown(
            **{name: _factor_score(row, name) for name in _US_STOCK_FACTOR_NAMES},
        )
    else:
        breakdown = StockScoreBreakdown(
            **{name: _factor_score(row, name) for name in _STOCK_FACTOR_NAMES},
        )
    return ScoreResult(
        symbol=row.symbol,
        market=market,
        as_of=date_.fromisoformat(row.as_of),
        profile=ScoreProfile(row.profile),
        symbol_type=symbol_type,
        composite=row.composite,
        band=ScoreBand(row.band),
        breakdown=breakdown,
        partial_factors=tuple(row.partial_factors),
        regime_caveat=row.regime_caveat,
        non_trading_caveat=row.non_trading_caveat,
        normalization_caveat=row.normalization_caveat,
    )


_ALL_FACTOR_NAME_SETS: tuple[tuple[str, ...], ...] = (
    _STOCK_FACTOR_NAMES,
    _US_STOCK_FACTOR_NAMES,
    _ETF_FACTOR_NAMES,
)


def _factor_columns(result: ScoreResult) -> dict[str, float | str | None]:
    """Every factor column this row could ever carry (stock/US-stock/ETF names, across all three
    breakdown shapes), so a re-run that switches a symbol's classification (should never happen
    in practice, but never silently leaves a stale value in a column the new breakdown doesn't
    populate) always writes a clean row — unpopulated columns for the other two shapes are
    explicitly `None`, not left untouched."""
    columns: dict[str, float | str | None] = {}
    breakdown = result.breakdown
    if isinstance(breakdown, EtfScoreBreakdown):
        factor_names = _ETF_FACTOR_NAMES
    elif isinstance(breakdown, UsStockScoreBreakdown):
        factor_names = _US_STOCK_FACTOR_NAMES
    else:
        factor_names = _STOCK_FACTOR_NAMES
    for name in factor_names:
        factor: FactorScore = getattr(breakdown, name)
        columns[f"{name}_percentile"] = factor.percentile
        columns[f"{name}_description"] = factor.description
        columns[f"{name}_unavailable_reason"] = factor.unavailable_reason
    for other_names in _ALL_FACTOR_NAME_SETS:
        for name in other_names:
            if name in factor_names:  # "momentum" is shared by every breakdown — never clear it
                continue
            columns[f"{name}_percentile"] = None
            columns[f"{name}_description"] = ""
            columns[f"{name}_unavailable_reason"] = None
    return columns


async def upsert_score(session: AsyncSession, result: ScoreResult) -> None:
    """Insert or replace this symbol's cached row for `(symbol, market, profile, as_of)` — a
    re-run for the same day overwrites the prior computation rather than accumulating history,
    matching the "same-day snapshot cache" shape the table docstring describes."""
    existing = await session.scalar(
        select(StockScoreRow).where(
            StockScoreRow.symbol == result.symbol,
            StockScoreRow.market == result.market.value,
            StockScoreRow.profile == result.profile.value,
            StockScoreRow.as_of == result.as_of.isoformat(),
        )
    )
    now = datetime.now(UTC)
    factor_columns = _factor_columns(result)
    if existing is None:
        session.add(
            StockScoreRow(
                id=str(uuid.uuid4()),
                symbol=result.symbol,
                market=result.market.value,
                as_of=result.as_of.isoformat(),
                profile=result.profile.value,
                symbol_type=result.symbol_type.value,
                composite=result.composite,
                band=result.band.value,
                partial_factors=list(result.partial_factors),
                regime_caveat=result.regime_caveat,
                non_trading_caveat=result.non_trading_caveat,
                normalization_caveat=result.normalization_caveat,
                computed_at=now,
                **factor_columns,
            )
        )
    else:
        existing.symbol_type = result.symbol_type.value
        existing.composite = result.composite
        existing.band = result.band.value
        existing.partial_factors = list(result.partial_factors)
        existing.regime_caveat = result.regime_caveat
        existing.non_trading_caveat = result.non_trading_caveat
        existing.normalization_caveat = result.normalization_caveat
        existing.computed_at = now
        for column, value in factor_columns.items():
            setattr(existing, column, value)
    await session.flush()


async def get_cached_score(
    session: AsyncSession,
    *,
    symbol: str,
    market: Market,
    profile: ScoreProfile,
    as_of: date_ | None = None,
) -> ScoreResult | None:
    """Point lookup for the detail-page tool: the cached row for `(symbol, market, profile)`,
    scoped to `as_of` when given, else the most recently computed row on file. Returns `None`
    when no row exists yet — the caller (the tool) decides whether that means "not computed for
    today" or something staler, by comparing the returned `ScoreResult.as_of` against the
    caller's notion of "today"; this seam never guesses at staleness itself."""
    stmt: Select[tuple[StockScoreRow]] = select(StockScoreRow).where(
        StockScoreRow.symbol == symbol,
        StockScoreRow.market == market.value,
        StockScoreRow.profile == profile.value,
    )
    if as_of is not None:
        stmt = stmt.where(StockScoreRow.as_of == as_of.isoformat())
    else:
        stmt = stmt.order_by(StockScoreRow.as_of.desc())
    row = await session.scalar(stmt.limit(1))
    return _row_to_result(row) if row is not None else None


async def _latest_as_of(
    session: AsyncSession, *, market: Market, profile: ScoreProfile
) -> str | None:
    stmt = select(func.max(StockScoreRow.as_of)).where(
        StockScoreRow.market == market.value,
        StockScoreRow.profile == profile.value,
    )
    result: str | None = await session.scalar(stmt)
    return result


async def get_leaderboard(  # noqa: PLR0913 — one keyword-only field per read-scoping dimension
    session: AsyncSession,
    *,
    market: Market,
    profile: ScoreProfile,
    direction: LeaderboardDirection,
    limit: int,
    universe: LeaderboardUniverse = "stock",
    as_of: date_ | None = None,
) -> tuple[ScoreResult, ...]:
    """The leaderboard read: sort the already-batch-computed whole-universe cache by `composite`
    and slice the top `limit` — never a live rescan. `direction="bullish"` sorts descending
    (strongest composite first); `"bearish"` sorts ascending (weakest first). Rows with a `None`
    composite (nothing available that day) are excluded — they can neither lead nor trail a
    ranking they have no number in. Scoped to `as_of` when given, else the latest `as_of` on file
    for this `(market, profile)`, which is what makes a plain "top 20" query resolve to today's
    batch pass without the caller having to know the date.

    `universe="stock"` (default) vs `"etf"` — a plain equity and any ETF (passive/active/
    leveraged/inverse) are scored on entirely different factor sets (see `StockScoreBreakdown`/
    `EtfScoreBreakdown`), so one combined ranking is not a fair comparison: an ETF that is only
    computable on a single available factor (e.g. `liquidity_flow` alone, every other factor
    unavailable that day) can land a composite of 100 on that one factor and outrank a stock with
    a real five-factor blend — a genuinely different question ("best-scoring stock" vs
    "best-scoring ETF") answered by two separate rankings, never one mixed list."""
    resolved_as_of = (
        as_of.isoformat()
        if as_of is not None
        else await _latest_as_of(session, market=market, profile=profile)
    )
    if resolved_as_of is None:
        return ()
    symbol_type_filter = (
        StockScoreRow.symbol_type.in_(_ETF_SYMBOL_TYPES)
        if universe == "etf"
        else StockScoreRow.symbol_type == SymbolType.STOCK.value
    )
    stmt: Select[tuple[StockScoreRow]] = select(StockScoreRow).where(
        StockScoreRow.market == market.value,
        StockScoreRow.profile == profile.value,
        StockScoreRow.as_of == resolved_as_of,
        StockScoreRow.composite.is_not(None),
        symbol_type_filter,
    )
    stmt = (
        stmt.order_by(StockScoreRow.composite.desc())
        if direction == "bullish"
        else stmt.order_by(StockScoreRow.composite.asc())
    )
    rows: Sequence[StockScoreRow] = (await session.scalars(stmt.limit(limit))).all()
    return tuple(_row_to_result(row) for row in rows)


__all__ = [
    "LeaderboardDirection",
    "get_cached_score",
    "get_leaderboard",
    "upsert_score",
]
