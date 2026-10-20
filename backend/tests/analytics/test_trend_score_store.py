"""`trend_scores` relational store tests over an in-memory SQLite schema — upsert-is-idempotent-
per-day, point lookup (latest + scoped-`as_of`), and the ranged history read the chart needs.
No live services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from analytics.models import TrendDimensionResult, TrendScoreResult
from analytics.trend_score_store import (
    get_cached_trend_score,
    get_trend_score_history,
    upsert_trend_score,
)
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys
from schemas.enums import Market


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _dimension(
    name: str, value: float | None = 60.0, reason: str | None = None
) -> TrendDimensionResult:
    return TrendDimensionResult(
        name=name,
        value=value,
        description=f"{name} description",
        unavailable_reason=reason,
    )


def _result(symbol: str, as_of: date, *, composite: float | None = 62.5) -> TrendScoreResult:
    return TrendScoreResult(
        symbol=symbol,
        market=Market.TW,
        as_of=as_of,
        composite=composite,
        dimensions=(
            _dimension("technical"),
            _dimension("chip_flow"),
            _dimension("margin_credit"),
            _dimension("broker_concentration", value=None, reason="not computed by the batch"),
            _dimension("sector_rotation"),
            _dimension("taiex_correlation"),
        ),
        unavailable_dimensions=("broker_concentration",),
        non_trading_caveat="Descriptive trend-strength read only — not a recommendation.",
    )


async def test_upsert_then_get_cached_round_trips(session_factory: async_sessionmaker) -> None:
    as_of = date(2026, 9, 22)
    result = _result("2330", as_of)
    async with session_factory() as session:
        await upsert_trend_score(session, result)
        await session.commit()

    async with session_factory() as session:
        cached = await get_cached_trend_score(session, symbol="2330", market=Market.TW)
    assert cached is not None
    assert cached.symbol == "2330"
    assert cached.as_of == as_of
    assert cached.composite == pytest.approx(62.5)
    assert cached.unavailable_dimensions == ("broker_concentration",)
    by_name = {d.name: d for d in cached.dimensions}
    assert by_name["technical"].value == pytest.approx(60.0)
    assert by_name["broker_concentration"].value is None
    assert by_name["broker_concentration"].unavailable_reason == "not computed by the batch"


async def test_upsert_same_day_overwrites_not_accumulates(
    session_factory: async_sessionmaker,
) -> None:
    as_of = date(2026, 9, 22)
    async with session_factory() as session:
        await upsert_trend_score(session, _result("2330", as_of, composite=50.0))
        await session.commit()
    async with session_factory() as session:
        await upsert_trend_score(session, _result("2330", as_of, composite=80.0))
        await session.commit()

    async with session_factory() as session:
        history = await get_trend_score_history(
            session, symbol="2330", market=Market.TW, start_date=as_of, end_date=as_of
        )
    assert len(history) == 1
    assert history[0].composite == pytest.approx(80.0)


async def test_get_cached_trend_score_scoped_as_of(session_factory: async_sessionmaker) -> None:
    day1, day2 = date(2026, 9, 21), date(2026, 9, 22)
    async with session_factory() as session:
        await upsert_trend_score(session, _result("2330", day1, composite=40.0))
        await upsert_trend_score(session, _result("2330", day2, composite=70.0))
        await session.commit()

    async with session_factory() as session:
        latest = await get_cached_trend_score(session, symbol="2330", market=Market.TW)
        scoped = await get_cached_trend_score(session, symbol="2330", market=Market.TW, as_of=day1)
    assert latest is not None
    assert latest.as_of == day2
    assert scoped is not None
    assert scoped.as_of == day1
    assert scoped.composite == pytest.approx(40.0)


async def test_get_cached_trend_score_returns_none_when_absent(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        cached = await get_cached_trend_score(session, symbol="9999", market=Market.TW)
    assert cached is None


async def test_get_trend_score_history_ranged_ascending(
    session_factory: async_sessionmaker,
) -> None:
    dates = [date(2026, 9, day) for day in (18, 19, 20, 21, 22)]
    async with session_factory() as session:
        for i, as_of in enumerate(dates):
            await upsert_trend_score(session, _result("2330", as_of, composite=float(i * 10)))
        await session.commit()

    async with session_factory() as session:
        history = await get_trend_score_history(
            session,
            symbol="2330",
            market=Market.TW,
            start_date=dates[1],
            end_date=dates[3],
        )
    assert [row.as_of for row in history] == dates[1:4]
    assert [row.composite for row in history] == [10.0, 20.0, 30.0]


async def test_get_trend_score_history_scoped_to_symbol_and_market(
    session_factory: async_sessionmaker,
) -> None:
    as_of = date(2026, 9, 22)
    async with session_factory() as session:
        await upsert_trend_score(session, _result("2330", as_of))
        await upsert_trend_score(session, _result("2454", as_of))
        await session.commit()

    async with session_factory() as session:
        history = await get_trend_score_history(
            session, symbol="2330", market=Market.TW, start_date=as_of, end_date=as_of
        )
    assert len(history) == 1
    assert history[0].symbol == "2330"
