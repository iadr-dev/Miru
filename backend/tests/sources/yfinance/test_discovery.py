"""Tests for the discovery/market-overview capability domain — `symbol_search`,
`symbol_lookup`, the calendars trio, `sector_overview`, `industry_overview`,
`market_status`. Recorded fixtures, no live network."""

from __future__ import annotations

import pandas as pd
import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.yfinance import YFinanceAdapter

from ._helpers import patch_run_sync


async def test_fetch_symbol_search(monkeypatch: pytest.MonkeyPatch) -> None:
    quotes = [
        {
            "symbol": "AAPL",
            "exchange": "NMS",
            "shortname": "Apple Inc.",
            "longname": "Apple Inc.",
            "quoteType": "EQUITY",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "score": 17_200_500.0,
        }
    ]
    patch_run_sync(monkeypatch, _fetch_search_blocking=quotes)
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(
        Capability.SYMBOL_SEARCH, sym, tls_policy=TLSPolicy.STRICT, query="Apple"
    )

    assert result.health.status is HealthStatus.OK
    results = result.payload["results"]
    assert results[0]["symbol"] == "AAPL"  # type: ignore[index]
    assert results[0]["query"] == "Apple"  # type: ignore[index]


async def test_fetch_symbol_search_degrades_when_no_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_run_sync(monkeypatch, _fetch_search_blocking=[])
    adapter = YFinanceAdapter()
    sym = Symbol(id="ZZZZZZ", market=Market.US)

    result = await adapter.fetch(Capability.SYMBOL_SEARCH, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE


async def test_fetch_symbol_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = pd.DataFrame(
        [{"exchange": "NMS", "quoteType": "EQUITY", "shortName": "Apple Inc."}], index=["AAPL"]
    )
    frame.index.name = "symbol"
    patch_run_sync(monkeypatch, _fetch_lookup_blocking=frame)
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.SYMBOL_LOOKUP, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    results = result.payload["results"]
    assert results[0]["instrument_type"] == "equity"  # type: ignore[index]


async def test_fetch_symbol_lookup_degrades_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_run_sync(monkeypatch, _fetch_lookup_blocking=pd.DataFrame())
    adapter = YFinanceAdapter()
    sym = Symbol(id="ZZZZZZ", market=Market.US)

    result = await adapter.fetch(Capability.SYMBOL_LOOKUP, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE


async def test_fetch_earnings_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = pd.DataFrame(
        [
            {
                "Company": "NVIDIA Corporation",
                "Event Start Date": pd.Timestamp("2026-08-26", tz="UTC"),
                "Timing": "Time Not Supplied",
                "EPS Estimate": 1.0,
                "Reported EPS": None,
                "Surprise(%)": None,
            }
        ],
        index=pd.Index(["NVDA"], name="Symbol"),
    )
    calendars = {"earnings": frame, "ipo": pd.DataFrame(), "economic": pd.DataFrame()}
    patch_run_sync(monkeypatch, _fetch_calendars_blocking=calendars)
    adapter = YFinanceAdapter()
    sym = Symbol(id="NVDA", market=Market.US)

    result = await adapter.fetch(Capability.EARNINGS_CALENDAR, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert rows[0]["symbol"] == "NVDA"  # type: ignore[index]
    assert rows[0]["company"] == "NVIDIA Corporation"  # type: ignore[index]


async def test_fetch_ipo_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = pd.DataFrame(
        [
            {
                "Company": "Ticketplus Ltd.",
                "Exchange": "NMS",
                "Filing Date": pd.Timestamp("2026-08-01"),
                "Date": pd.Timestamp("2026-08-15"),
                "Price From": 10.0,
                "Price To": 12.0,
                "Price": None,
                "Currency": "USD",
                "Shares": 1_000_000.0,
                "Action": "Expected",
            }
        ],
        index=pd.Index(["TP"], name="Symbol"),
    )
    calendars = {"earnings": pd.DataFrame(), "ipo": frame, "economic": pd.DataFrame()}
    patch_run_sync(monkeypatch, _fetch_calendars_blocking=calendars)
    adapter = YFinanceAdapter()
    sym = Symbol(id="TP", market=Market.US)

    result = await adapter.fetch(Capability.IPO_CALENDAR, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert rows[0]["action"] == "Expected"  # type: ignore[index]


async def test_fetch_economic_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = pd.DataFrame(
        [
            {
                "Region": "US",
                "Event Time": pd.Timestamp("2026-08-31", tz="UTC"),
                "For": "Jul",
                "Actual": None,
                "Expected": 61.0,
                "Last": 61.6,
                "Revised": None,
            }
        ],
        index=pd.Index(["Consumer Sentiment*"], name="Event"),
    )
    calendars = {"earnings": pd.DataFrame(), "ipo": pd.DataFrame(), "economic": frame}
    patch_run_sync(monkeypatch, _fetch_calendars_blocking=calendars)
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(Capability.ECONOMIC_CALENDAR, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert rows[0]["market"] == Market.GLOBAL.value  # type: ignore[index]
    assert rows[0]["event"] == "Consumer Sentiment*"  # type: ignore[index]


async def test_fetch_calendar_degrades_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    calendars = {"earnings": pd.DataFrame(), "ipo": pd.DataFrame(), "economic": pd.DataFrame()}
    patch_run_sync(monkeypatch, _fetch_calendars_blocking=calendars)
    adapter = YFinanceAdapter()
    sym = Symbol(id="AAPL", market=Market.US)

    result = await adapter.fetch(Capability.EARNINGS_CALENDAR, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE


async def test_fetch_sector_overview(monkeypatch: pytest.MonkeyPatch) -> None:
    top_companies = pd.DataFrame(
        [{"name": "NVIDIA Corporation", "rating": "Strong Buy", "market weight": 0.177}],
        index=["NVDA"],
    )
    frames = {"top_companies": top_companies, "top_etfs": None, "top_mutual_funds": None}
    patch_run_sync(monkeypatch, _fetch_sector_blocking=frames)
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(
        Capability.SECTOR_OVERVIEW, sym, tls_policy=TLSPolicy.STRICT, sector_key="technology"
    )

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert rows[0]["symbol"] == "NVDA"  # type: ignore[index]
    assert rows[0]["row_kind"] == "company"  # type: ignore[index]


async def test_fetch_sector_overview_requires_sector_key() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(Capability.SECTOR_OVERVIEW, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_industry_overview(monkeypatch: pytest.MonkeyPatch) -> None:
    top_companies = pd.DataFrame(
        [{"name": "NVIDIA Corporation", "rating": "Strong Buy", "market weight": 0.44}],
        index=["NVDA"],
    )
    patch_run_sync(
        monkeypatch,
        _fetch_industry_blocking=(
            {
                "top_companies": top_companies,
                "top_growth_companies": None,
                "top_performing_companies": None,
            },
            "technology",
        ),
    )
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(
        Capability.INDUSTRY_OVERVIEW,
        sym,
        tls_policy=TLSPolicy.STRICT,
        industry_key="semiconductors",
    )

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert rows[0]["sector_key"] == "technology"  # type: ignore[index]
    assert rows[0]["row_kind"] == "company"  # type: ignore[index]


async def test_fetch_industry_overview_requires_industry_key() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(Capability.INDUSTRY_OVERVIEW, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_market_status_us(monkeypatch: pytest.MonkeyPatch) -> None:
    status = {"status": "closed", "message": "U.S. markets closed"}
    patch_run_sync(monkeypatch, _fetch_market_status_blocking=status)
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(Capability.MARKET_STATUS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    assert result.payload["status"]["status"] == "closed"  # type: ignore[index]
