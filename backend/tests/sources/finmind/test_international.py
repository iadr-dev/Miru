"""Tests for FinMind International-Markets-category dataset mappers — recorded fixtures, no
live network."""

from __future__ import annotations

import pytest

from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture

from ._helpers import patch_client, settings


async def test_fetch_us_stock_info_declares_us_market(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "us_stock_info.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="^DJI", market=Market.US)

    result = await adapter.fetch(Capability.US_STOCK_INFO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.PUBLIC
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["market"] == "US"  # type: ignore[index]


async def test_fetch_us_stock_info_rejects_tw_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    """International capabilities never serve a TW-market symbol — `fetch()` must return
    unavailable rather than silently reinterpreting the market."""
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.US_STOCK_INFO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_us_stock_price_uses_dollar_dji_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """US-market `data_id` values are quote symbols like `^DJI`, never a TW-style numeric id."""
    body = load_fixture("finmind", "us_stock_price_djia.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="^DJI", market=Market.US)

    result = await adapter.fetch(Capability.US_STOCK_PRICE, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["symbol"] == "^DJI"  # type: ignore[index]
    assert rows[0]["adj_close"] == 26289.98  # type: ignore[index]


async def test_fetch_us_stock_price_minute_is_sponsor_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "us_stock_price_minute_djia.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="^DJI", market=Market.US)

    result = await adapter.fetch(Capability.US_STOCK_PRICE_MINUTE, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["close"] == 30000.5  # type: ignore[index]


async def test_fetch_uk_stock_info_declares_gb_market(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "uk_stock_info.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="BP.L", market=Market.GB)

    result = await adapter.fetch(Capability.UK_STOCK_INFO, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["market"] == "GB"  # type: ignore[index]


async def test_fetch_uk_stock_price(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "uk_stock_price_bp.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="BP.L", market=Market.GB)

    result = await adapter.fetch(Capability.UK_STOCK_PRICE, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["symbol"] == "BP.L"  # type: ignore[index]


async def test_fetch_europe_stock_info_declares_eu_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "europe_stock_info.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="AALB.AS", market=Market.EU)

    result = await adapter.fetch(Capability.EUROPE_STOCK_INFO, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["market"] == "EU"  # type: ignore[index]


async def test_fetch_europe_stock_price(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "europe_stock_price_aalb.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="AALB.AS", market=Market.EU)

    result = await adapter.fetch(Capability.EUROPE_STOCK_PRICE, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["symbol"] == "AALB.AS"  # type: ignore[index]


async def test_fetch_japan_stock_info_includes_sector(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike US/UK/Europe, `JapanStockInfo` carries a `Sector` column."""
    body = load_fixture("finmind", "japan_stock_info.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="7203.T", market=Market.JP)

    result = await adapter.fetch(Capability.JAPAN_STOCK_INFO, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["sector"] == "Automobiles"  # type: ignore[index]
    assert rows[0]["market"] == "JP"  # type: ignore[index]


async def test_fetch_japan_stock_price(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "japan_stock_price_7203.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="7203.T", market=Market.JP)

    result = await adapter.fetch(Capability.JAPAN_STOCK_PRICE, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["symbol"] == "7203.T"  # type: ignore[index]
