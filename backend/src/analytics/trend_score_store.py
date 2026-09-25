"""The relational read/write seam over `trend_scores` — the cached six-dimension 趨勢評分
(trend score) table the daily batch job (`analytics/scoring_batch.py::job_trend_score_batch`)
writes and both the detail-page point-lookup tool and the trend-chart's ranged-history read use.

Mirrors `analytics/scoring_store.py`'s shape (caller owns the session/transaction boundary; every
read reconstructs a real `TrendScoreResult`/`TrendDimensionResult` domain object, never a raw
dict) with one genuine difference: `stock_scores` is a same-day snapshot cache a fresh batch pass
*replaces*, while `trend_scores` is a real time series a caller reads a date RANGE from for a
chart (see `TrendScoreRow`'s own docstring, `db/models.py`) — hence `get_trend_score_history`
below, a read shape `scoring_store.py` has no counterpart for.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from datetime import date as date_

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.models import TrendDimensionResult, TrendScoreResult
from db.models import TrendScoreRow
from schemas.enums import Market

# The six dimension names, in `trend_score.py`'s own fixed order — used to walk the flat
# per-dimension column triplets on `TrendScoreRow` without repeating each name six times.
_TW_DIMENSION_NAMES: tuple[str, ...] = (
    "technical",
    "chip_flow",
    "margin_credit",
    "broker_concentration",
    "sector_rotation",
    "taiex_correlation",
)
# `Market.US`'s six dimension names — four TW-only dimensions replaced by their US-native
# counterparts (see `analytics/trend_score.py::US_DIMENSION_WEIGHTS`'s own comment).
_US_DIMENSION_NAMES: tuple[str, ...] = (
    "technical",
    "institutional_ownership",
    "insider_sentiment",
    "analyst_revision",
    "sector_momentum",
    "taiex_correlation",
)
_ALL_DIMENSION_NAME_SETS: tuple[tuple[str, ...], ...] = (_TW_DIMENSION_NAMES, _US_DIMENSION_NAMES)


def _dimension_names_for(market: Market) -> tuple[str, ...]:
    return _US_DIMENSION_NAMES if market is Market.US else _TW_DIMENSION_NAMES


def _dimension_result(row: TrendScoreRow, name: str) -> TrendDimensionResult:
    value = getattr(row, f"{name}_value")
    description = getattr(row, f"{name}_description")
    unavailable_reason = getattr(row, f"{name}_unavailable_reason")
    return TrendDimensionResult(
        name=name,
        value=value,
        description=description,
        unavailable_reason=unavailable_reason,
    )


def _row_to_result(row: TrendScoreRow) -> TrendScoreResult:
    market = Market(row.market)
    return TrendScoreResult(
        symbol=row.symbol,
        market=market,
        as_of=date_.fromisoformat(row.as_of),
        composite=row.composite,
        dimensions=tuple(_dimension_result(row, name) for name in _dimension_names_for(market)),
        unavailable_dimensions=tuple(row.unavailable_dimensions),
        non_trading_caveat=row.non_trading_caveat,
    )


def _dimension_columns(result: TrendScoreResult) -> dict[str, float | str | None]:
    """Every dimension column this row could ever carry (both the TW and US six-dimension name
    sets), keyed by the dimension's own `name` field — a dimension absent from `result.dimensions`
    (should not happen given `trend_score.py` always returns all six for the result's market, but
    defensively handled) leaves that dimension's columns at their defaults rather than raising.
    Columns for the OTHER market's dimension names are explicitly cleared to `None`, mirroring
    `scoring_store.py::_factor_columns`'s "never leave a stale value from a prior classification"
    contract — see that function's own docstring."""
    columns: dict[str, float | str | None] = {}
    by_name = {dimension.name: dimension for dimension in result.dimensions}
    own_names = _dimension_names_for(result.market)
    for name in own_names:
        dimension = by_name.get(name)
        if dimension is None:
            columns[f"{name}_value"] = None
            columns[f"{name}_description"] = ""
            columns[f"{name}_unavailable_reason"] = None
            continue
        columns[f"{name}_value"] = dimension.value
        columns[f"{name}_description"] = dimension.description
        columns[f"{name}_unavailable_reason"] = dimension.unavailable_reason
    for other_names in _ALL_DIMENSION_NAME_SETS:
        for name in other_names:
            if name in own_names:  # "technical"/"taiex_correlation" are shared — never clear them
                continue
            columns[f"{name}_value"] = None
            columns[f"{name}_description"] = ""
            columns[f"{name}_unavailable_reason"] = None
    return columns


async def upsert_trend_score(session: AsyncSession, result: TrendScoreResult) -> None:
    """Insert or replace this symbol's row for `(symbol, market, as_of)` — a re-run for the same
    day overwrites that day's prior computation rather than accumulating a duplicate, but distinct
    `as_of` days genuinely coexist (this table is a time series, not a same-day snapshot cache —
    see `TrendScoreRow`'s own docstring)."""
    existing = await session.scalar(
        select(TrendScoreRow).where(
            TrendScoreRow.symbol == result.symbol,
            TrendScoreRow.market == result.market.value,
            TrendScoreRow.as_of == result.as_of.isoformat(),
        )
    )
    now = datetime.now(UTC)
    dimension_columns = _dimension_columns(result)
    if existing is None:
        session.add(
            TrendScoreRow(
                id=str(uuid.uuid4()),
                symbol=result.symbol,
                market=result.market.value,
                as_of=result.as_of.isoformat(),
                composite=result.composite,
                unavailable_dimensions=list(result.unavailable_dimensions),
                non_trading_caveat=result.non_trading_caveat,
                computed_at=now,
                **dimension_columns,
            )
        )
    else:
        existing.composite = result.composite
        existing.unavailable_dimensions = list(result.unavailable_dimensions)
        existing.non_trading_caveat = result.non_trading_caveat
        existing.computed_at = now
        for column, value in dimension_columns.items():
            setattr(existing, column, value)
    await session.flush()


async def get_cached_trend_score(
    session: AsyncSession,
    *,
    symbol: str,
    market: Market,
    as_of: date_ | None = None,
) -> TrendScoreResult | None:
    """Point lookup for the detail-page tool: the row for `(symbol, market)`, scoped to `as_of`
    when given, else the most recently computed row on file. Returns `None` when no row exists
    yet — same "the caller decides what staleness means" contract as
    `scoring_store.get_cached_score`."""
    stmt: Select[tuple[TrendScoreRow]] = select(TrendScoreRow).where(
        TrendScoreRow.symbol == symbol,
        TrendScoreRow.market == market.value,
    )
    if as_of is not None:
        stmt = stmt.where(TrendScoreRow.as_of == as_of.isoformat())
    else:
        stmt = stmt.order_by(TrendScoreRow.as_of.desc())
    row = await session.scalar(stmt.limit(1))
    return _row_to_result(row) if row is not None else None


async def get_trend_score_history(
    session: AsyncSession,
    *,
    symbol: str,
    market: Market,
    start_date: date_,
    end_date: date_,
) -> tuple[TrendScoreResult, ...]:
    """The ranged read the trend-score chart needs: every row for `(symbol, market)` with
    `start_date <= as_of <= end_date`, ascending by `as_of` — the one read shape
    `scoring_store.py` has no counterpart for, since `stock_scores` never keeps more than one row
    per `(symbol, market, profile)` at a time."""
    stmt: Select[tuple[TrendScoreRow]] = (
        select(TrendScoreRow)
        .where(
            TrendScoreRow.symbol == symbol,
            TrendScoreRow.market == market.value,
            TrendScoreRow.as_of >= start_date.isoformat(),
            TrendScoreRow.as_of <= end_date.isoformat(),
        )
        .order_by(TrendScoreRow.as_of.asc())
    )
    rows: Sequence[TrendScoreRow] = (await session.scalars(stmt)).all()
    return tuple(_row_to_result(row) for row in rows)


__all__ = [
    "get_cached_trend_score",
    "get_trend_score_history",
    "upsert_trend_score",
]
