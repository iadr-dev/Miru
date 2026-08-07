"""Tests for the holders/options capability domain — `options_chain`,
`institutional_holders`, `insider_activity`, `fund_holdings`. Recorded fixtures, no live
network."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.yfinance import YFinanceAdapter

from ._helpers import empty_frames, patch_run_sync


class _FakeOptions:
    def __init__(self, calls: pd.DataFrame, puts: pd.DataFrame) -> None:
        self.calls = calls
        self.puts = puts


async def test_fetch_options_chain_us(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = empty_frames()
    frames["options"] = ("2026-08-07",)
    calls = pd.DataFrame(
        [
            {
                "contractSymbol": "AAPL260807C00300000",
                "strike": 300.0,
                "lastPrice": 5.0,
                "bid": 4.8,
                "ask": 5.2,
                "volume": 100,
                "openInterest": 200,
                "impliedVolatility": 0.25,
                "inTheMoney": True,
            }
        ]
    )
    puts = pd.DataFrame()
    patch_run_sync(
        monkeypatch,
        _fetch_ticker_frames_blocking=frames,
        _fetch_option_chain_blocking=lambda *_a: _FakeOptions(calls, puts),
    )
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.OPTIONS_CHAIN, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    contracts = result.payload["contracts"]
    assert contracts[0]["side"] == "call"  # type: ignore[index]
    assert contracts[0]["strike"] == 300.0  # type: ignore[index]


async def test_fetch_options_chain_degrades_when_no_expiries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=empty_frames())
    adapter = YFinanceAdapter()
    sym = Symbol(id="NOOPT", market=Market.US)

    result = await adapter.fetch(Capability.OPTIONS_CHAIN, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_institutional_holders_us(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = empty_frames()
    frames["institutional_holders"] = pd.DataFrame(
        [
            {
                "Date Reported": pd.Timestamp("2026-03-31"),
                "Holder": "Blackrock Inc.",
                "pctHeld": 0.08,
                "Shares": 1_000_000,
                "Value": 300_000_000.0,
                "pctChange": -0.01,
            }
        ]
    )
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=frames)
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.INSTITUTIONAL_HOLDERS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    holders = result.payload["holders"]
    assert holders[0]["holder_type"] == "institutional"  # type: ignore[index]
    assert holders[0]["holder_name"] == "Blackrock Inc."  # type: ignore[index]


async def test_fetch_institutional_holders_degrades_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=empty_frames())
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.INSTITUTIONAL_HOLDERS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE


async def test_fetch_insider_activity_us(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = empty_frames()
    frames["insider_transactions"] = pd.DataFrame(
        [
            {
                "Insider": "COOK TIMOTHY D",
                "Position": "CEO",
                "Text": "Sale",
                "Shares": 30104,
                "Value": None,
                "Start Date": pd.Timestamp("2026-06-15"),
                "Ownership": "D",
            }
        ]
    )
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=frames)
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.INSIDER_ACTIVITY, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    transactions = result.payload["transactions"]
    assert transactions[0]["insider_name"] == "COOK TIMOTHY D"  # type: ignore[index]
    assert transactions[0]["start_date"] == date(2026, 6, 15).isoformat()  # type: ignore[index]


async def test_fetch_insider_activity_degrades_when_empty_outside_us(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=empty_frames())
    adapter = YFinanceAdapter()
    sym = Symbol(id="7203.T", market=Market.JP)

    result = await adapter.fetch(Capability.INSIDER_ACTIVITY, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_fund_holdings(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFundsData:
        def __init__(self) -> None:
            self.top_holdings = pd.DataFrame(
                [{"Name": "NVIDIA Corp", "Holding Percent": 0.0758}], index=["NVDA"]
            )
            self.sector_weightings = {"technology": 0.61, "financial_services": 0.02}

    frames = empty_frames()
    frames["funds_data"] = _FakeFundsData()
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=frames)
    adapter = YFinanceAdapter()
    sym = Symbol(id="QQQ", market=Market.US)

    result = await adapter.fetch(Capability.FUND_HOLDINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    holdings = result.payload["holdings"]
    weightings = result.payload["sector_weightings"]
    assert holdings[0]["holding_symbol"] == "NVDA"  # type: ignore[index]
    assert weightings[0]["sector"] == "technology"  # type: ignore[index]


async def test_fetch_fund_holdings_degrades_for_non_fund(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=empty_frames())
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.FUND_HOLDINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
