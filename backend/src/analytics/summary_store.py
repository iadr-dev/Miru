"""The relational read/write seam over `stock_summaries` — the cached AI-generated headline/
narrative/SWOT table `analytics/summary_generation.py` writes and the `get_stock_summary` tool
reads.

Mirrors `analytics/scoring_store.py`'s shape: the caller owns the session/transaction boundary
(this module never opens or commits its own session), and every read reconstructs a real
`StockSummaryResult`/`StockSwot` domain object — never a raw dict.

**Point lookup only, no "latest" fallback** — unlike `scoring_store.get_cached_score` (which
falls back to the most recent row when `as_of` is omitted), `get_cached_summary` always requires
`trade_date` and never guesses: a summary is only valid for the exact score it was grounded in,
so serving a stale summary for a different day's score would silently misattribute its narrative
to numbers that no longer match.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from datetime import date as date_

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.models import StockSummaryResult, StockSwot
from db.models import StockSummaryRow
from schemas.enums import Market


def _row_to_result(row: StockSummaryRow) -> StockSummaryResult:
    return StockSummaryResult(
        symbol=row.symbol,
        market=Market(row.market),
        as_of=date_.fromisoformat(row.trade_date),
        headline=row.headline,
        narrative=row.narrative,
        swot=StockSwot(
            strengths=tuple(row.swot_strengths),
            weaknesses=tuple(row.swot_weaknesses),
            opportunities=tuple(row.swot_opportunities),
            threats=tuple(row.swot_threats),
        ),
        non_trading_caveat=row.non_trading_caveat,
    )


async def upsert_summary(session: AsyncSession, result: StockSummaryResult) -> None:
    """Insert or replace this symbol's cached row for `(symbol, market, trade_date)` — a re-run
    for the same score date overwrites the prior generation rather than accumulating history,
    matching `stock_scores`'s same-day-snapshot cache shape (here keyed to the score's own date)."""
    existing = await session.scalar(
        select(StockSummaryRow).where(
            StockSummaryRow.symbol == result.symbol,
            StockSummaryRow.market == result.market.value,
            StockSummaryRow.trade_date == result.as_of.isoformat(),
        )
    )
    now = datetime.now(UTC)
    if existing is None:
        session.add(
            StockSummaryRow(
                id=str(uuid.uuid4()),
                symbol=result.symbol,
                market=result.market.value,
                trade_date=result.as_of.isoformat(),
                headline=result.headline,
                narrative=result.narrative,
                swot_strengths=list(result.swot.strengths),
                swot_weaknesses=list(result.swot.weaknesses),
                swot_opportunities=list(result.swot.opportunities),
                swot_threats=list(result.swot.threats),
                non_trading_caveat=result.non_trading_caveat,
                generated_at=now,
            )
        )
    else:
        existing.headline = result.headline
        existing.narrative = result.narrative
        existing.swot_strengths = list(result.swot.strengths)
        existing.swot_weaknesses = list(result.swot.weaknesses)
        existing.swot_opportunities = list(result.swot.opportunities)
        existing.swot_threats = list(result.swot.threats)
        existing.non_trading_caveat = result.non_trading_caveat
        existing.generated_at = now
    await session.flush()


async def get_cached_summary(
    session: AsyncSession,
    *,
    symbol: str,
    market: Market,
    trade_date: date_,
) -> StockSummaryResult | None:
    """Point lookup for the exact `(symbol, market, trade_date)` — `trade_date` is the caller's
    `ScoreResult.as_of`, never "today" and never "latest on file". Returns `None` when no
    summary has been generated yet for that exact score date."""
    row = await session.scalar(
        select(StockSummaryRow).where(
            StockSummaryRow.symbol == symbol,
            StockSummaryRow.market == market.value,
            StockSummaryRow.trade_date == trade_date.isoformat(),
        )
    )
    return _row_to_result(row) if row is not None else None


__all__ = ["get_cached_summary", "upsert_summary"]
