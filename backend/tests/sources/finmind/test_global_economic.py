"""Tests for FinMind Global-Economic-Data-category dataset mappers — recorded fixtures, no
live network."""

from __future__ import annotations

import pytest

from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture

from ._helpers import patch_client, settings


async def test_fetch_exchange_rate_is_tw_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "exchange_rate_usd.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="USD", market=Market.TW)

    result = await adapter.fetch(Capability.EXCHANGE_RATE, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["symbol"] == "USD"  # type: ignore[index]
    assert rows[0]["market"] == "TW"  # type: ignore[index]


async def test_fetch_central_bank_rate_is_global_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`InterestRate` is a multi-country panel with no single-country home — modeled
    `Market.GLOBAL`."""
    body = load_fixture("finmind", "central_bank_rate_fed.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="FED", market=Market.GLOBAL)

    result = await adapter.fetch(Capability.CENTRAL_BANK_RATE, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["market"] == "GLOBAL"  # type: ignore[index]
    assert rows[0]["country"] == "FED"  # type: ignore[index]


async def test_fetch_gold_price_is_global_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "gold_price.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="", market=Market.GLOBAL)

    result = await adapter.fetch(Capability.GOLD_PRICE, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["price"] == 1500.5  # type: ignore[index]
    assert rows[0]["market"] == "GLOBAL"  # type: ignore[index]
    # Confirmed empirically: FinMind returns a full timestamp ("2020-01-01 00:05:00") for this
    # dataset's `date` column, not a bare date — pydantic's `date` field rejects that outright
    # (`date_from_datetime_inexact`) unless truncated first.
    assert rows[0]["date"] == "2020-01-01"  # type: ignore[index]


async def test_fetch_crude_oil_prices_lag_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spot price, not real-time — the currentness caveat must say so explicitly."""
    body = load_fixture("finmind", "crude_oil_prices_wti.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="WTI", market=Market.GLOBAL)

    result = await adapter.fetch(Capability.CRUDE_OIL_PRICES, sym, tls_policy=TLSPolicy.STRICT)

    assert "spot" in result.currentness_caveat.lower()
    assert "real-time" in result.currentness_caveat.lower()
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["price"] == 61.2  # type: ignore[index]


async def test_fetch_government_bond_yield_is_us_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "government_bonds_yield_us10y.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="United States 10-Year", market=Market.US)

    result = await adapter.fetch(Capability.GOVERNMENT_BOND_YIELD, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["value"] == 1.88  # type: ignore[index]
    assert rows[0]["market"] == "US"  # type: ignore[index]


async def test_fetch_fear_greed_index_is_us_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "cnn_fear_greed_index.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(Capability.FEAR_GREED_INDEX, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["fear_greed_emotion"] == "greed"  # type: ignore[index]
    assert rows[0]["market"] == "US"  # type: ignore[index]
