"""TWSE MIS adapter tests — recorded fixture, no live network.

Includes a dedicated test proving `fetch()` makes exactly one upstream request per call
(single-shot, never a poll/stream) — the hard governance rule for this adapter.
"""

from __future__ import annotations

import httpx
import pytest

import sources.twse_mis as twse_mis_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.twse_mis import TwseMisAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(twse_mis_module, "build_async_client", make_json_client_factory(body))


def test_supports_price_intraday_tw_only() -> None:
    adapter = TwseMisAdapter()
    assert adapter.supports() == {(Capability.PRICE_INTRADAY, Market.TW)}


def test_validate_rejects_non_list_msg_array() -> None:
    adapter = TwseMisAdapter()
    with pytest.raises(ValueError, match="not a list"):
        adapter.validate({"msgArray": "nope"})


async def test_fetch_returns_one_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("twse_mis", "getStockInfo_2330.json")
    _patch_client(monkeypatch, body)
    adapter = TwseMisAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_INTRADAY, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "TWSE_MIS"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    snapshot = result.payload["snapshot"]
    assert snapshot["symbol"] == "2330"  # type: ignore[index]
    assert snapshot["last_price"] == 596.0  # type: ignore[index]


async def test_fetch_empty_msg_array_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("twse_mis", "getStockInfo_empty.json")
    _patch_client(monkeypatch, body)
    adapter = TwseMisAdapter()
    sym = Symbol(id="9999", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_INTRADAY, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_unsupported_capability() -> None:
    adapter = TwseMisAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_is_single_shot_never_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    """One `fetch()` call must issue exactly one upstream HTTP request — no internal loop."""
    call_count = 0
    body = load_fixture("twse_mis", "getStockInfo_2330.json")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=body)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(twse_mis_module, "build_async_client", factory)
    adapter = TwseMisAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_INTRADAY, sym, tls_policy=TLSPolicy.STRICT)

    assert call_count == 1
    assert result.health.status is HealthStatus.OK
