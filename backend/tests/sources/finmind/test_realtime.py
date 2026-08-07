"""Tests for FinMind Real-Time-category dataset mappers — recorded fixtures, no live network."""

from __future__ import annotations

import pytest

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
