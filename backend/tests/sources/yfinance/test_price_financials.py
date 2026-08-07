"""Tests for the non-TW price/financials capability domain — `price_eod`, `financials`,
`dividends`, `split_price`. Recorded fixtures, no live network."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.yfinance import YFinanceAdapter

from ._helpers import empty_frames, patch_run_sync


async def test_fetch_price_eod_us(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = empty_frames()
    frames["history"] = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [105.0],
            "Low": [99.0],
            "Close": [104.0],
            "Volume": [1_000_000],
        },
        index=pd.DatetimeIndex([pd.Timestamp("2026-08-03")], name="Date"),
    )
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=frames)
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    bars = result.payload["bars"]
    assert bars[0]["close"] == 104.0  # type: ignore[index]
    assert bars[0]["trade_date"] == date(2026, 8, 3).isoformat()  # type: ignore[index]


async def test_fetch_price_eod_degrades_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=empty_frames())
    adapter = YFinanceAdapter()
    sym = Symbol(id="NOSUCH", market=Market.US)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_financials_us(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = empty_frames()
    frames["financials"] = pd.DataFrame(
        {pd.Timestamp("2025-09-30"): [100.0]}, index=["Total Revenue"]
    )
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=frames)
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.FINANCIALS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    lines = result.payload["lines"]
    assert lines[0]["item"] == "Total Revenue"  # type: ignore[index]
    assert lines[0]["value"] == 100.0  # type: ignore[index]


async def test_fetch_financials_degrades_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=empty_frames())
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.FINANCIALS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE


async def test_fetch_dividends_gb(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = empty_frames()
    frames["dividends"] = pd.Series(
        [0.26], index=pd.DatetimeIndex([pd.Timestamp("2026-02-09")], name="Date")
    )
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=frames)
    adapter = YFinanceAdapter()
    sym = Symbol(id="BP.L", market=Market.GB)

    result = await adapter.fetch(Capability.DIVIDENDS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    events = result.payload["events"]
    assert events[0]["cash_dividend"] == 0.26  # type: ignore[index]


async def test_fetch_split_price_jp(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = empty_frames()
    frames["splits"] = pd.Series(
        [4.0], index=pd.DatetimeIndex([pd.Timestamp("2020-08-31")], name="Date")
    )
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=frames)
    adapter = YFinanceAdapter()
    sym = Symbol(id="7203.T", market=Market.JP)

    result = await adapter.fetch(Capability.SPLIT_PRICE, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert rows[0]["split_ratio"] == 4.0  # type: ignore[index]


async def test_fetch_split_price_degrades_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=empty_frames())
    adapter = YFinanceAdapter()
    sym = Symbol(id="AALB.AS", market=Market.EU)

    result = await adapter.fetch(Capability.SPLIT_PRICE, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
