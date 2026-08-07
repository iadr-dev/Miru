"""TPEX OpenAPI adapter tests — recorded fixture, no live network."""

from __future__ import annotations

import pytest

import sources.tpex_openapi as tpex_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.tpex_openapi import TpexOpenApiAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(tpex_module, "build_async_client", make_json_client_factory(body))


def test_supports_price_eod_tw_only() -> None:
    adapter = TpexOpenApiAdapter()
    assert adapter.supports() == {(Capability.PRICE_EOD, Market.TW)}


def test_validate_rejects_non_list_rows() -> None:
    adapter = TpexOpenApiAdapter()
    with pytest.raises(ValueError, match="not a list"):
        adapter.validate({"rows": "nope"})


async def test_fetch_filters_to_requested_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("tpex_openapi", "mainboard_daily_close_quotes.json")
    _patch_client(monkeypatch, body)
    adapter = TpexOpenApiAdapter()
    sym = Symbol(id="6547", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "TPEX_OPENAPI"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    bar = result.payload["bar"]
    assert bar["symbol"] == "6547"  # type: ignore[index]
    assert bar["close"] == 85.5  # type: ignore[index]


async def test_fetch_symbol_not_in_dump_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("tpex_openapi", "mainboard_daily_close_quotes.json")
    _patch_client(monkeypatch, body)
    adapter = TpexOpenApiAdapter()
    sym = Symbol(id="9999", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_unsupported_capability() -> None:
    adapter = TpexOpenApiAdapter()
    sym = Symbol(id="6547", market=Market.TW)

    result = await adapter.fetch(Capability.FINANCIALS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
