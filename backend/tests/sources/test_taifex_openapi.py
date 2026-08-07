"""TAIFEX OpenAPI adapter tests — recorded fixture, no live network."""

from __future__ import annotations

import pytest

import sources.taifex_openapi as taifex_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.taifex_openapi import TaifexOpenApiAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(taifex_module, "build_async_client", make_json_client_factory(body))


def test_supports_derivatives_oi_tw_only() -> None:
    adapter = TaifexOpenApiAdapter()
    assert adapter.supports() == {(Capability.DERIVATIVES_OI, Market.TW)}


def test_validate_rejects_non_list_rows() -> None:
    adapter = TaifexOpenApiAdapter()
    with pytest.raises(ValueError, match="not a list"):
        adapter.validate({"rows": "nope"})


async def test_fetch_filters_to_requested_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("taifex_openapi", "daily_market_report_fut.json")
    _patch_client(monkeypatch, body)
    adapter = TaifexOpenApiAdapter()
    sym = Symbol(id="TX", market=Market.TW)

    result = await adapter.fetch(Capability.DERIVATIVES_OI, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "TAIFEX_OPENAPI"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    record = result.payload["record"]
    assert record["symbol"] == "TX"  # type: ignore[index]
    assert record["open_interest"] == 45678  # type: ignore[index]


async def test_fetch_contract_not_in_dump_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("taifex_openapi", "daily_market_report_fut.json")
    _patch_client(monkeypatch, body)
    adapter = TaifexOpenApiAdapter()
    sym = Symbol(id="ZZZ", market=Market.TW)

    result = await adapter.fetch(Capability.DERIVATIVES_OI, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_unsupported_capability() -> None:
    adapter = TaifexOpenApiAdapter()
    sym = Symbol(id="TX", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
