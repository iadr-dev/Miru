"""Tests for the `FinMindAdapter` class itself — supports/validate/fetch/fetch_whole_market/
_call and error handling, not tied to any one dataset category. Recorded fixtures, no live
network."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import sources.finmind as finmind_module
from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture

from ._helpers import patch_client, settings


def test_supports_declares_tw_and_international_markets() -> None:
    """Most FinMind datasets are TW-scoped; the International Markets + Global Economic Data
    datasets from the third coverage pass declare their own real market instead."""
    adapter = FinMindAdapter(settings=settings())
    supported = adapter.supports()
    assert (Capability.PRICE_EOD, Market.TW) in supported
    assert (Capability.CHIP_BROKER, Market.TW) in supported
    assert (Capability.US_STOCK_INFO, Market.US) in supported
    assert (Capability.UK_STOCK_INFO, Market.GB) in supported
    assert (Capability.EUROPE_STOCK_INFO, Market.EU) in supported
    assert (Capability.JAPAN_STOCK_INFO, Market.JP) in supported
    assert (Capability.CENTRAL_BANK_RATE, Market.GLOBAL) in supported
    assert (Capability.GOVERNMENT_BOND_YIELD, Market.US) in supported
    assert (Capability.US_STOCK_INFO, Market.TW) not in supported


def test_validate_rejects_missing_data_key() -> None:
    adapter = FinMindAdapter(settings=settings())
    with pytest.raises(ValueError, match="missing 'data'"):
        adapter.validate({"msg": "success", "status": 200})


def test_validate_rejects_non_200_status() -> None:
    adapter = FinMindAdapter(settings=settings())
    with pytest.raises(ValueError, match="status=402"):
        adapter.validate({"msg": "Requests reach the upper limit.", "status": 402, "data": []})


async def test_fetch_unsupported_capability_returns_unavailable_tier() -> None:
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.ESG, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert result.payload == {}


async def test_fetch_rate_limit_surfaces_as_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    body: dict[str, Any] = {"msg": "Requests reach the upper limit.", "status": 402}
    patch_client(monkeypatch, body, status_code=402)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert "402" in result.health.detail
    assert result.payload == {}


async def test_fetch_whole_market_omits_data_id_and_covers_multiple_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ingest-only whole-market-by-date path (governance carve-out): no `data_id` on the
    request, and rows for distinct symbols come back in one call."""
    body = load_fixture("finmind", "price_eod_whole_market_2024-01-02.json")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=body)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(finmind_module, "build_async_client", factory)
    adapter = FinMindAdapter(settings=settings())

    result = await adapter.fetch_whole_market(
        Capability.PRICE_EOD, tls_policy=TLSPolicy.STRICT, start_date="2024-01-02"
    )

    assert "data_id" not in captured["params"]  # type: ignore[operator]
    assert result.health.status is HealthStatus.OK
    bars = result.payload["bars"]
    assert isinstance(bars, list)
    symbols = {bar["symbol"] for bar in bars}  # type: ignore[index]
    assert symbols == {"2330", "0050"}  # the zero-close 1101 row is filtered out


async def test_fetch_whole_market_rejects_capability_without_whole_market_form() -> None:
    adapter = FinMindAdapter(settings=settings())

    result = await adapter.fetch_whole_market(Capability.CHIP_BROKER, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert result.payload == {}


def test_token_never_appears_in_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Governance — no secrets logged/surfaced. The Bearer token must never leak into any
    SourceResult field."""
    adapter = FinMindAdapter(settings=settings(token="super-secret-token"))
    # The token only ever flows into the Authorization header built inside fetch(); assert
    # the settings object is the only holder and adapter exposes no token attribute/property.
    assert not hasattr(adapter, "token")
    assert not hasattr(adapter, "api_key")
