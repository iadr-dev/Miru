"""`get_trend_score` driven through `dispatch_tool` against a seeded `trend_scores` cache row and
a stubbed `Capability.CHIP_BROKER` router — no live provider, no upstream fetch.

Covers: no-cached-row -> `found=False`; cached row hit -> five dimensions served from the cache
for an unentitled caller with `broker_concentration` disclosed as locked (no live fetch attempted
implicitly checked by the router only being asked for what an entitled caller needs); an entitled
caller gets a live `broker_concentration` fetch blended into the composite; history is returned
independent of whether today's row exists.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from analytics.models import TrendDimensionResult, TrendScoreResult
from analytics.trend_score_store import upsert_trend_score
from dataaccess.router import DataAccessRouter
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import StubAdapter, make_router
from tools.base import ToolContext, dispatch_tool
from tools.trend_score_tools import GetTrendScoreOutput, build_registration_table


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
        name=name, value=value, description=f"{name} description", unavailable_reason=reason
    )


def _seeded_result(as_of: date) -> TrendScoreResult:
    return TrendScoreResult(
        symbol="2330",
        market=Market.TW,
        as_of=as_of,
        composite=62.5,
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


def _empty_chip_broker_router() -> tuple[DataAccessRouter, StubAdapter]:
    symbol = Symbol(id="2330", market=Market.TW)
    result = SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="chip caveat",
        payload={"rows": []},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    return make_router(symbol, {Capability.CHIP_BROKER: result})


async def test_no_cached_row_returns_found_false(session_factory: async_sessionmaker) -> None:
    router, _adapter = _empty_chip_broker_router()
    table = build_registration_table(session_factory, router)
    tool = table[0][0]

    result = await dispatch_tool(
        tool,
        tool.input_model(id="2330", market=Market.TW),
        ToolContext(tier=Tier.FREE),
    )

    assert isinstance(result, GetTrendScoreOutput)
    assert result.found is False
    assert "not yet computed" in result.message or "not yet" in result.message


async def test_unentitled_caller_gets_cached_dimensions_with_concentration_locked(
    session_factory: async_sessionmaker,
) -> None:
    as_of = date(2026, 9, 22)
    async with session_factory() as session:
        await upsert_trend_score(session, _seeded_result(as_of))
        await session.commit()

    router, adapter = _empty_chip_broker_router()
    table = build_registration_table(session_factory, router)
    tool = table[0][0]

    result = await dispatch_tool(
        tool,
        tool.input_model(id="2330", market=Market.TW),
        ToolContext(tier=Tier.FREE),
    )

    assert isinstance(result, GetTrendScoreOutput)
    assert result.found is True
    assert result.as_of == as_of
    by_name = {d.name: d for d in result.dimensions}
    assert by_name["broker_concentration"].locked is True
    assert by_name["broker_concentration"].value is None
    assert by_name["technical"].locked is False
    # No live Sponsor fetch was attempted on an unentitled caller's behalf.
    assert adapter.calls == []


async def test_entitled_caller_gets_live_broker_concentration_blended_in(
    session_factory: async_sessionmaker,
) -> None:
    as_of = date(2026, 9, 22)
    async with session_factory() as session:
        await upsert_trend_score(session, _seeded_result(as_of))
        await session.commit()

    symbol = Symbol(id="2330", market=Market.TW)
    rows = [
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": as_of.isoformat(),
            "broker_id": "1001",
            "broker_name": "Fubon",
            "price": 100.0,
            "buy": 5000,
            "sell": 1000,
        }
    ]
    result_payload = SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="chip caveat",
        payload={"rows": rows},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, adapter = make_router(symbol, {Capability.CHIP_BROKER: result_payload})
    table = build_registration_table(session_factory, router)
    tool = table[0][0]

    result = await dispatch_tool(
        tool,
        tool.input_model(id="2330", market=Market.TW),
        ToolContext(tier=Tier.PREMIUM),
    )

    assert isinstance(result, GetTrendScoreOutput)
    assert result.found is True
    by_name = {d.name: d for d in result.dimensions}
    assert by_name["broker_concentration"].locked is False
    assert by_name["broker_concentration"].value is not None
    assert adapter.calls[0][0] is Capability.CHIP_BROKER
    # Composite re-blended over all six dimensions now that concentration is available.
    assert result.composite is not None
    assert "broker_concentration" not in result.unavailable_dimensions


async def test_history_close_is_populated_from_real_price_eod(
    session_factory: async_sessionmaker,
) -> None:
    """`TrendScoreHistoryPoint.close` must be the real closing price for that trading day (the
    chart's dual-axis price overlay), never left `None` when a real bar exists."""
    as_of = date(2026, 9, 22)
    async with session_factory() as session:
        await upsert_trend_score(session, _seeded_result(as_of))
        await session.commit()

    symbol = Symbol(id="2330", market=Market.TW)
    chip_broker_result = SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="chip caveat",
        payload={"rows": []},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    price_eod_result = SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="price caveat",
        payload={
            "bars": [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "trade_date": as_of.isoformat(),
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 1000,
                }
            ]
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, _adapter = make_router(
        symbol,
        {Capability.CHIP_BROKER: chip_broker_result, Capability.PRICE_EOD: price_eod_result},
    )
    table = build_registration_table(session_factory, router)
    tool = table[0][0]

    result = await dispatch_tool(
        tool,
        tool.input_model(id="2330", market=Market.TW),
        ToolContext(tier=Tier.FREE),
    )

    assert isinstance(result, GetTrendScoreOutput)
    assert len(result.history) == 1
    assert result.history[0].close == 100.5


async def test_history_returned_independent_of_todays_row(
    session_factory: async_sessionmaker,
) -> None:
    today = date.today()  # the 30-day window is relative to now, so seed relative dates too
    dates = [today - timedelta(days=offset) for offset in (3, 2, 1)]
    async with session_factory() as session:
        for as_of in dates:
            await upsert_trend_score(session, _seeded_result(as_of))
        await session.commit()

    router, _adapter = _empty_chip_broker_router()
    table = build_registration_table(session_factory, router)
    tool = table[0][0]

    result = await dispatch_tool(
        tool,
        tool.input_model(id="2330", market=Market.TW, days=30),
        ToolContext(tier=Tier.FREE),
    )

    assert isinstance(result, GetTrendScoreOutput)
    assert len(result.history) == len(dates)
    assert [point.trade_date for point in result.history] == dates
