"""Multi-source builder (M8A/B/C) tests — using the stub router
(no real `sources/*`, never a live network call in the default suite)."""

from __future__ import annotations

from datetime import UTC, datetime

from context.builder import BuildOptions, build_canonical_context
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.context.conftest import make_router


def _ok_result(cap: Capability) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=f"caveat for {cap.value}",
        payload={"bars": [{"close": 1.0}]},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_default_build_covers_m8a_capabilities_only() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router = make_router(
        symbol,
        {
            Capability.PRICE_EOD: _ok_result(Capability.PRICE_EOD),
            Capability.FINANCIALS: _ok_result(Capability.FINANCIALS),
            Capability.DIVIDENDS: _ok_result(Capability.DIVIDENDS),
            Capability.INSTITUTIONAL_FLOW: _ok_result(Capability.INSTITUTIONAL_FLOW),
            Capability.ANALYST_CONSENSUS: _ok_result(Capability.ANALYST_CONSENSUS),
            Capability.SECTOR_CLASS: _ok_result(Capability.SECTOR_CLASS),
        },
    )

    canonical, health = await build_canonical_context(router, symbol)

    assert canonical.get(Capability.PRICE_EOD) is not None
    assert canonical.get(Capability.DERIVATIVES_OI) is None  # M8B not opted-in
    assert canonical.get(Capability.PRICE_INTRADAY) is None  # M8C not opted-in
    assert health.all_ok


async def test_m8b_derivatives_degrades_gracefully_when_no_adapter_configured() -> None:
    """No TAIFEX adapter exists yet (Phase 2 skipped it) — opting into M8B must still not
    crash; the gap shows up in SourceHealthReport as unreachable/unavailable, not an
    exception."""
    symbol = Symbol(id="2330", market=Market.TW)
    router = make_router(symbol, {Capability.PRICE_EOD: _ok_result(Capability.PRICE_EOD)})

    canonical, health = await build_canonical_context(
        router, symbol, options=BuildOptions(include_derivatives=True)
    )

    derivatives_record = canonical.get(Capability.DERIVATIVES_OI)
    assert derivatives_record is not None
    assert derivatives_record.result.source_id == "none"
    assert health.status_for(Capability.DERIVATIVES_OI) is HealthStatus.UNREACHABLE


async def test_m8c_stays_behind_validation_flag_by_default() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router = make_router(symbol, {Capability.PRICE_INTRADAY: _ok_result(Capability.PRICE_INTRADAY)})

    canonical_default, _ = await build_canonical_context(router, symbol)
    canonical_opted_in, _ = await build_canonical_context(
        router, symbol, options=BuildOptions(enable_taifex_mis_validation=True)
    )

    assert canonical_default.get(Capability.PRICE_INTRADAY) is None
    assert canonical_opted_in.get(Capability.PRICE_INTRADAY) is not None
