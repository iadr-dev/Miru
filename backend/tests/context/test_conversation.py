"""Conversation (M5N) tests — no raw payload ever leaks, every fact keeps its caveat."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from context.canonical import build_canonical_package
from context.conversation import ConversationFact, build_conversation_package
from context.observation import ObservationRun
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult

_SECRET_MARKER = "SECRET_UPSTREAM_ROW_VALUE_42"


def _result_with_sensitive_payload() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub currentness caveat",
        payload={"bars": [{"close": 100.0, "raw_note": _SECRET_MARKER}]},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def test_conversation_fact_has_no_payload_field() -> None:
    """`ConversationFact`'s schema itself carries no field that could hold a raw payload —
    frozen + extra='forbid' means no caller can smuggle one in either."""
    assert "payload" not in ConversationFact.model_fields


def test_build_conversation_package_never_leaks_raw_payload_values() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    canonical = build_canonical_package(
        symbol, {Capability.PRICE_EOD: _result_with_sensitive_payload()}
    )

    package = build_conversation_package(symbol, canonical=canonical)

    dumped = package.model_dump_json()
    assert _SECRET_MARKER not in dumped
    fact = package.fact_for(Capability.PRICE_EOD)
    assert fact is not None
    assert fact.currentness_caveat == "stub currentness caveat"
    assert fact.headline  # a derived summary, never the raw dict


def test_build_conversation_package_layers_observation_over_canonical() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    canonical_result = _result_with_sensitive_payload()
    canonical = build_canonical_package(symbol, {Capability.PRICE_EOD: canonical_result})

    fresher_result = SourceResult(
        source_id="STUB_FRESH",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.LIVE_ISH,
        fetched_at=datetime.now(UTC),
        currentness_caveat="fresher bounded observation caveat",
        payload={"bar": {"close": 605.0}},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    observation = ObservationRun(
        capability=Capability.PRICE_EOD,
        symbol=symbol,
        requested_at=datetime.now(UTC),
        result=fresher_result,
    )

    package = build_conversation_package(symbol, canonical=canonical, observations=(observation,))

    fact = package.fact_for(Capability.PRICE_EOD)
    assert fact is not None
    assert fact.source_id == "STUB_FRESH"  # observation wins over canonical for same capability
    assert fact.currentness_caveat == "fresher bounded observation caveat"


def test_non_trading_notice_always_present() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    package = build_conversation_package(symbol)

    assert package.non_trading_notice
    assert "not a trading signal" in package.non_trading_notice


@pytest.mark.governance
def test_conversation_package_is_frozen() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    package = build_conversation_package(symbol)

    with pytest.raises(ValidationError):
        package.symbol = Symbol(id="0050", market=Market.TW)  # type: ignore[misc]
