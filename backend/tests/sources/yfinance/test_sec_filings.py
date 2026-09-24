"""Tests for the `SEC_FILINGS` capability domain — recorded fixtures, no live network. US-only
(no equivalent regulatory-filing feed exists for any other market this codebase serves)."""

from __future__ import annotations

import pytest

from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.yfinance import YFinanceAdapter

from ._helpers import empty_frames, patch_run_sync

_ONE_FILING = [
    {
        "date": "2026-08-01",
        "type": "10-Q",
        "title": "Quarterly report",
        "exhibits": [{"type": "EX-10", "url": "https://www.sec.gov/example"}],
    }
]


async def test_fetch_sec_filings_returns_normalized_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = empty_frames()
    frames["sec_filings"] = _ONE_FILING
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=frames)
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.SEC_FILINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "YFINANCE"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    filings = result.payload["filings"]
    assert filings[0]["filing_type"] == "10-Q"  # type: ignore[index]
    assert filings[0]["edgar_url"] == "https://www.sec.gov/example"  # type: ignore[index]


async def test_fetch_sec_filings_no_filings_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = empty_frames()
    patch_run_sync(monkeypatch, _fetch_ticker_frames_blocking=frames)
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.SEC_FILINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_sec_filings_unavailable_for_tw() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.SEC_FILINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_sec_filings_unavailable_for_gb() -> None:
    """US-only per `_US_ONLY_SUPPORTED` — GB/JP/EU stay on `_NON_TW_SUPPORTED` alone, which does
    not include this capability, unlike the broader US-primary set (financials/analyst/etc.)."""
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.GB)

    result = await adapter.fetch(Capability.SEC_FILINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
