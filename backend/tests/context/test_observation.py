"""Bounded Observation (M5K) tests — one explicit call, no polling."""

from __future__ import annotations

from datetime import UTC, datetime

from context.observation import run_bounded_observation
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.context.conftest import make_router


def _result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.LIVE_ISH,
        fetched_at=datetime.now(UTC),
        currentness_caveat="bounded snapshot caveat",
        payload={"bar": {"close": 601.0}},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_run_bounded_observation_performs_exactly_one_fetch() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router = make_router(symbol, {Capability.PRICE_INTRADAY: _result()})

    run = await run_bounded_observation(router, Capability.PRICE_INTRADAY, symbol)

    assert run.result.health.status is HealthStatus.OK
    assert run.capability is Capability.PRICE_INTRADAY
    assert run.symbol == symbol
    assert "Bounded Observation" in run.bounded_note


async def test_run_bounded_observation_surfaces_unavailable_gracefully() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router = make_router(symbol, {})  # nothing configured for this capability

    run = await run_bounded_observation(router, Capability.DERIVATIVES_OI, symbol)

    assert run.result.source_id == "none"
    assert run.result.health.status is HealthStatus.UNREACHABLE
