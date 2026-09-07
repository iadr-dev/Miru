"""Tests for FinMind Real-Time-category dataset mappers — recorded fixtures, no live network."""

from __future__ import annotations

import httpx
import pytest

import sources.finmind as finmind_module
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture

from ._helpers import patch_client, settings


async def test_fetch_tick_snapshot_is_live_ish(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Real-Time snapshots use LIVE_ISH, not OFFICIAL_EOD — a bounded snapshot, not the
    official end-of-day aggregator."""
    body = load_fixture("finmind", "tick_snapshot_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.TICK_SNAPSHOT, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    assert result.timing_class is TimingClass.LIVE_ISH
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["tick_type"] == "1"  # type: ignore[index]


async def test_fetch_tick_snapshot_accepts_index_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """`taiwan_stock_tick_snapshot` accepts a 3-digit index code as `data_id`, not just a
    4-digit stock id — modeled as an unrestricted `Symbol.id`."""
    body = load_fixture("finmind", "tick_snapshot_2330.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="001", market=Market.TW)  # TAIEX index code

    result = await adapter.fetch(Capability.TICK_SNAPSHOT, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status.value == "ok"


async def test_fetch_futopt_tick_info_is_reference_roster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = load_fixture("finmind", "futopt_tick_info.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TX", market=Market.TW)

    result = await adapter.fetch(Capability.FUTOPT_TICK_INFO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.PUBLIC
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert {row["code"] for row in rows} == {"TX", "TXO"}  # type: ignore[index]


async def test_fetch_futures_snapshot_is_live_ish(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "futures_snapshot_txf.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TXF", market=Market.TW)

    result = await adapter.fetch(Capability.FUTURES_SNAPSHOT, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    assert result.timing_class is TimingClass.LIVE_ISH
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["symbol"] == "TXF"  # type: ignore[index]


async def test_fetch_options_snapshot_is_live_ish(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("finmind", "options_snapshot_txo.json")
    patch_client(monkeypatch, body)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TXO", market=Market.TW)

    result = await adapter.fetch(Capability.OPTIONS_SNAPSHOT, sym, tls_policy=TLSPolicy.STRICT)

    assert result.data_class is DataClass.SPONSOR
    assert result.timing_class is TimingClass.LIVE_ISH
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["symbol"] == "TXO"  # type: ignore[index]


@pytest.mark.parametrize(
    ("cap", "expected_path"),
    [
        (Capability.TICK_SNAPSHOT, "/taiwan_stock_tick_snapshot"),
        (Capability.FUTURES_SNAPSHOT, "/taiwan_futures_snapshot"),
        (Capability.OPTIONS_SNAPSHOT, "/taiwan_options_snapshot"),
    ],
)
async def test_realtime_snapshots_hit_their_own_dedicated_endpoint(
    monkeypatch: pytest.MonkeyPatch, cap: Capability, expected_path: str
) -> None:
    """Confirmed live: none of `taiwan_stock_tick_snapshot`/`taiwan_futures_snapshot`/
    `taiwan_options_snapshot` are in the generic `/data` endpoint's `dataset` enum (422 "Input
    should be ..."); FinMind serves every Real-Time-category dataset off its own dedicated path
    instead, the same `special_endpoint` mechanism `chip_institutional.py`'s
    `taiwan_stock_trading_daily_report` already uses."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={"data": []})

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(finmind_module, "build_async_client", factory)
    adapter = FinMindAdapter(settings=settings())
    sym = Symbol(id="TXF", market=Market.TW)

    await adapter.fetch(cap, sym, tls_policy=TLSPolicy.STRICT)

    assert captured["path"].endswith(expected_path)
