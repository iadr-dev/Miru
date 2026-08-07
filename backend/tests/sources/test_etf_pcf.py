"""ETF_PCF adapter tests — recorded fixtures, no live network (default profile)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import sources.etf_pcf as etf_pcf_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.etf_pcf import EtfPcfAdapter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "etf_pcf"


def _client_factory(list_body: object, fund_info_body: object) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        if "t187ap47_L" in str(request.url):
            return httpx.Response(200, json=fund_info_body)
        return httpx.Response(200, json=list_body)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_supports_declares_tw_etf_holdings() -> None:
    adapter = EtfPcfAdapter()
    assert adapter.supports() == {(Capability.ETF_HOLDINGS, Market.TW)}


def test_validate_rejects_non_object_info() -> None:
    adapter = EtfPcfAdapter()
    with pytest.raises(ValueError, match="not an object"):
        adapter.validate({"info": ["not", "an", "object"]})


async def test_fetch_merges_list_and_fund_info(monkeypatch: pytest.MonkeyPatch) -> None:
    list_body = _load("list.json")
    fund_info_body = _load("fund_info.json")
    monkeypatch.setattr(
        etf_pcf_module, "build_async_client", _client_factory(list_body, fund_info_body)
    )
    adapter = EtfPcfAdapter()
    sym = Symbol(id="0050", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_HOLDINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "ETF_PCF"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    assert result.currentness_caveat
    info = result.payload["info"]
    assert isinstance(info, dict)
    assert info["symbol"] == "0050"
    assert info["fund_name"] == "元大台灣50"
    assert info["tracked_index"] == "富時臺灣證券交易所臺灣50指數"
    assert info["units_outstanding"] == 5827000000
    assert info["listed_date"] == "2003-06-30"


async def test_fetch_handles_dual_listing_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `<br>`-joined dual-code/date row (NTD/RMB share classes) must resolve to the first
    (primary) code/date, never a raw `<br>`-joined HTML fragment leaking into the model."""
    list_body = _load("list.json")
    fund_info_body = _load("fund_info.json")
    monkeypatch.setattr(
        etf_pcf_module, "build_async_client", _client_factory(list_body, fund_info_body)
    )
    adapter = EtfPcfAdapter()
    # The upstream row packs two codes as "006205(新臺幣)<br>00625K(人民幣)" — the parser must
    # key on the first (primary NTD) code, not the raw joined string or the second code.
    sym = Symbol(id="006205(新臺幣)", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_HOLDINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    info = result.payload["info"]
    assert isinstance(info, dict)
    assert "<br>" not in str(info["fund_name"])
    assert "<br>" not in str(info["tracked_index"] or "")

    other_class_sym = Symbol(id="00625K(人民幣)", market=Market.TW)
    other_result = await adapter.fetch(
        Capability.ETF_HOLDINGS, other_class_sym, tls_policy=TLSPolicy.STRICT
    )
    # The RMB share class code is never indexed on its own — it was dropped, not fabricated.
    assert other_result.health.status is HealthStatus.STALE


async def test_fetch_falls_back_to_list_only_when_fund_info_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_body = _load("list.json")
    monkeypatch.setattr(etf_pcf_module, "build_async_client", _client_factory(list_body, []))
    adapter = EtfPcfAdapter()
    sym = Symbol(id="0056", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_HOLDINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    info = result.payload["info"]
    assert isinstance(info, dict)
    assert info["fund_name"] == "元大高股息"
    assert info["units_outstanding"] is None


async def test_fetch_unknown_symbol_returns_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    list_body = _load("list.json")
    fund_info_body = _load("fund_info.json")
    monkeypatch.setattr(
        etf_pcf_module, "build_async_client", _client_factory(list_body, fund_info_body)
    )
    adapter = EtfPcfAdapter()
    sym = Symbol(id="9999", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_HOLDINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_unsupported_capability_returns_unavailable_tier() -> None:
    adapter = EtfPcfAdapter()
    sym = Symbol(id="0050", market=Market.TW)

    result = await adapter.fetch(Capability.NEWS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
    assert result.payload == {}


async def test_fetch_degrades_honestly_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(etf_pcf_module, "build_async_client", factory)
    adapter = EtfPcfAdapter()
    sym = Symbol(id="0050", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_HOLDINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {}
