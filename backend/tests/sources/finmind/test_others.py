"""Tests for FinMind Others-category dataset mappers, plus the active-ETF family — recorded
fixtures, no live network."""

from __future__ import annotations

import httpx
import pytest

import sources.finmind as finmind_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture

from ._helpers import patch_client, settings


async def test_fetch_active_etf_info_is_public_and_free_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "active_etf_info.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="00980A", market=Market.TW)

    result = await adapter.fetch(Capability.ACTIVE_ETF_INFO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    info = result.payload["info"]
    assert isinstance(info, list)
    assert {row["symbol"] for row in info} == {"00980A", "00981A"}  # type: ignore[index]


async def test_fetch_active_etf_holding_is_sponsor_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "active_etf_holding_00980a.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="00980A", market=Market.TW)

    result = await adapter.fetch(
        Capability.ACTIVE_ETF_HOLDING,
        sym,
        tls_policy=TLSPolicy.STRICT,
        start_date="2025-05-05",
        end_date="2025-05-08",
    )

    assert result.data_class is DataClass.SPONSOR
    holdings = result.payload["holdings"]
    assert isinstance(holdings, list)
    assert holdings[1]["weight"] == 9.2  # type: ignore[index]


async def test_fetch_active_etf_holding_change_is_sponsor_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "active_etf_holding_change_00980a.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="00980A", market=Market.TW)

    result = await adapter.fetch(
        Capability.ACTIVE_ETF_HOLDING_CHANGE,
        sym,
        tls_policy=TLSPolicy.STRICT,
        start_date="2025-05-07",
        end_date="2025-05-07",
    )

    assert result.data_class is DataClass.SPONSOR
    changes = result.payload["changes"]
    assert isinstance(changes, list)
    assert changes[0]["buy"] == 20000  # type: ignore[index]
    assert changes[1]["sell"] == 5000  # type: ignore[index]


async def test_fetch_whole_market_active_etf_holding_change_omits_data_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ingest-only whole-market form for the two Sponsor-tier active-ETF datasets."""
    body = load_fixture("finmind", "active_etf_holding_change_00980a.json")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=body)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(finmind_module, "build_async_client", factory)
    adapter = FinMindAdapter(settings=settings())

    result = await adapter.fetch_whole_market(
        Capability.ACTIVE_ETF_HOLDING_CHANGE, tls_policy=TLSPolicy.STRICT, start_date="2025-05-07"
    )

    assert "data_id" not in captured["params"]  # type: ignore[operator]
    assert result.health.status is HealthStatus.OK


async def test_fetch_stock_news_is_finmind_own_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    """FinMind's own `TaiwanStockNews`, distinct from the RSS/GDELT/Finnhub aggregator."""
    body = load_fixture("finmind", "stock_news_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.STOCK_NEWS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.PUBLIC
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["source"] == "cnyes"  # type: ignore[index]


async def test_fetch_business_indicator_is_market_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "business_indicator.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.BUSINESS_INDICATOR, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["monitoring_color"] == "yellow-red"  # type: ignore[index]


async def test_fetch_industry_chain_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "industry_chain_classification.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(
        Capability.INDUSTRY_CHAIN_CLASSIFICATION, sym, tls_policy=TLSPolicy.STRICT
    )

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["sub_industry"] == "IC設計"  # type: ignore[index]
