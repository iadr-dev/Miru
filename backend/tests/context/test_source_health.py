"""Source Health (M5Q) tests — diagnostics aggregation, including the "no adapter configured"
degrade-gracefully path."""

from __future__ import annotations

from datetime import UTC, datetime

from context.canonical import build_canonical_package
from context.source_health import build_source_health_report, source_health_from_canonical
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult


def _ok_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="ok caveat",
        payload={"bars": []},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _unavailable_result() -> SourceResult:
    return SourceResult(
        source_id="none",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="no adapter configured",
        payload={},
        health=SourceHealth(status=HealthStatus.UNREACHABLE, detail="no configured adapters"),
    )


def test_build_source_health_report_all_ok() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    report = build_source_health_report(symbol, {Capability.PRICE_EOD: _ok_result()})

    assert report.all_ok
    assert report.status_for(Capability.PRICE_EOD) is HealthStatus.OK


def test_build_source_health_report_degrades_gracefully_on_missing_adapter() -> None:
    """A capability with no configured/available adapter (e.g. TAIFEX derivatives today)
    shows up as UNREACHABLE in the report — never raises."""
    symbol = Symbol(id="2330", market=Market.TW)
    report = build_source_health_report(
        symbol,
        {
            Capability.PRICE_EOD: _ok_result(),
            Capability.DERIVATIVES_OI: _unavailable_result(),
        },
    )

    assert not report.all_ok
    assert report.status_for(Capability.DERIVATIVES_OI) is HealthStatus.UNREACHABLE
    assert report.status_for(Capability.PRICE_EOD) is HealthStatus.OK


def test_source_health_from_canonical_matches_build() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    results = {Capability.PRICE_EOD: _ok_result()}
    canonical = build_canonical_package(symbol, results)

    from_canonical = source_health_from_canonical(canonical)
    from_build = build_source_health_report(symbol, results)

    assert from_canonical.entries == from_build.entries
