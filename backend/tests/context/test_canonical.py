"""Canonical (M5F) tests — immutability is asserted directly, not assumed from "no setter"."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from context.canonical import CanonicalPackage, CanonicalRecord, build_canonical_package
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult


def _stub_result(cap: Capability) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=f"stub caveat for {cap.value}",
        payload={"bars": [{"close": 100.0}]},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def test_build_canonical_package_preserves_results() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    results = {Capability.PRICE_EOD: _stub_result(Capability.PRICE_EOD)}

    package = build_canonical_package(symbol, results)

    assert package.version == 1
    assert package.get(Capability.PRICE_EOD) is not None
    assert package.get(Capability.FINANCIALS) is None


@pytest.mark.governance
def test_canonical_package_is_frozen_and_raises_on_mutation() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    results = {Capability.PRICE_EOD: _stub_result(Capability.PRICE_EOD)}
    package = build_canonical_package(symbol, results)

    with pytest.raises(ValidationError):
        package.version = 2  # type: ignore[misc]


@pytest.mark.governance
def test_canonical_record_is_frozen_and_raises_on_mutation() -> None:
    record = CanonicalRecord(
        capability=Capability.PRICE_EOD, result=_stub_result(Capability.PRICE_EOD)
    )

    with pytest.raises(ValidationError):
        record.capability = Capability.FINANCIALS  # type: ignore[misc]


def test_new_version_never_mutates_self_and_increments() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    original = build_canonical_package(
        symbol, {Capability.PRICE_EOD: _stub_result(Capability.PRICE_EOD)}
    )
    updated_records = (
        CanonicalRecord(
            capability=Capability.FINANCIALS, result=_stub_result(Capability.FINANCIALS)
        ),
    )

    new_package = original.new_version(updated_records)

    assert new_package is not original
    assert new_package.version == original.version + 1
    assert original.version == 1  # untouched
    assert original.get(Capability.FINANCIALS) is None  # untouched
    assert new_package.get(Capability.FINANCIALS) is not None
    assert new_package.get(Capability.PRICE_EOD) is None  # new_version replaces, not merges


def test_canonical_package_extra_forbid() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    with pytest.raises(ValidationError):
        CanonicalPackage(
            symbol=symbol,
            built_at=datetime.now(UTC),
            records=(),
            unexpected_field="nope",  # type: ignore[call-arg]
        )
