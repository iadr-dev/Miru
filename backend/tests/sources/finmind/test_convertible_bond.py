"""Tests for FinMind Convertible-Bond-category dataset mappers — recorded fixtures, no live
network."""

from __future__ import annotations

import httpx
import pytest

import sources.finmind as finmind_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture

from ._helpers import patch_client, settings


async def test_fetch_cb_info_uses_cb_id_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "cb_info_15131.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="15131", market=Market.TW)

    result = await adapter.fetch(Capability.CB_INFO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["symbol"] == "15131"  # type: ignore[index]


async def test_fetch_cb_daily(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "cb_daily_15131.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="15131", market=Market.TW)

    result = await adapter.fetch(Capability.CB_DAILY, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["close"] == 105.5  # type: ignore[index]


async def test_fetch_cb_institutional_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "cb_institutional_flow_15131.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="15131", market=Market.TW)

    result = await adapter.fetch(Capability.CB_INSTITUTIONAL_FLOW, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["total_overbuy"] == 6.0  # type: ignore[index]


async def test_fetch_cb_daily_overview_put_fields_populated_only_during_announced_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The put/early-redemption fields are only populated while a put/early-redemption process
    is publicly announced — most rows have them empty, which is expected, not missing data."""
    body = load_fixture("finmind", "cb_daily_overview_15131.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="15131", market=Market.TW)

    result = await adapter.fetch(Capability.CB_DAILY_OVERVIEW, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["latest_put_price"] is None  # type: ignore[index]
    assert rows[1]["latest_put_price"] == 100.0  # type: ignore[index]


async def test_fetch_cb_monthly_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "cb_monthly_analysis_13166.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="13166", market=Market.TW)

    result = await adapter.fetch(Capability.CB_MONTHLY_ANALYSIS, sym, tls_policy=TLSPolicy.STRICT)

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["custody_balance"] == 100000  # type: ignore[index]


async def test_fetch_cb_put_provision_data_id_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`data_id` is optional for this dataset — the date-range-only call is documented as
    whole-market-capable."""
    body = load_fixture("finmind", "cb_put_provision.json")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=body)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(finmind_module, "build_async_client", factory)
    adapter = FinMindAdapter(settings=settings())

    result = await adapter.fetch_whole_market(
        Capability.CB_PUT_PROVISION, tls_policy=TLSPolicy.STRICT, start_date="2011-06-01"
    )

    assert "data_id" not in captured["params"]  # type: ignore[operator]
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["put_price"] == 100.0  # type: ignore[index]
