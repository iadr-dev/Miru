"""TPEX OpenAPI adapter tests — recorded fixture, no live network."""

from __future__ import annotations

import httpx
import pytest

import sources.tpex_openapi as tpex_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.tpex_openapi import (
    TpexOpenApiAdapter,
    _split_roc_date_range,
    fetch_whole_market_attention_stocks_tpex,
    fetch_whole_market_disposition_events_tpex,
)
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


def test_split_roc_date_range() -> None:
    start, end = _split_roc_date_range("1150907~1150915")
    assert start is not None
    assert end is not None
    assert start.isoformat() == "2026-09-07"
    assert end.isoformat() == "2026-09-15"


def test_split_roc_date_range_rejects_malformed_input() -> None:
    assert _split_roc_date_range("not-a-range") == (None, None)
    assert _split_roc_date_range(None) == (None, None)


async def test_fetch_whole_market_attention_stocks_tpex_maps_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("tpex_openapi", "trading_warning_information.json")
    _patch_client(monkeypatch, body)

    result = await fetch_whole_market_attention_stocks_tpex(tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "2061"  # type: ignore[index]
    assert row["flagged_date"] == "2026-09-04"  # type: ignore[index]
    assert row["security_type"] == "tpex"  # type: ignore[index]


async def test_fetch_whole_market_attention_stocks_tpex_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(tpex_module, "build_async_client", factory)

    result = await fetch_whole_market_attention_stocks_tpex(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {"rows": []}


async def test_fetch_whole_market_disposition_events_tpex_maps_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("tpex_openapi", "disposal_information.json")
    _patch_client(monkeypatch, body)

    result = await fetch_whole_market_disposition_events_tpex(tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "24552"  # type: ignore[index]
    assert row["period_start"] == "2026-09-07"  # type: ignore[index]
    assert row["period_end"] == "2026-09-15"  # type: ignore[index]
    assert row["security_type"] == "tpex"  # type: ignore[index]
    assert row["detail"] is None  # TPEX has no separate detail field
