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


async def test_fetch_sector_overview_includes_stats_and_industries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    top_companies = pd.DataFrame(
        [{"name": "NVIDIA Corporation", "rating": "Strong Buy", "market weight": 0.177}],
        index=["NVDA"],
    )
    industries = pd.DataFrame(
        [
            {"name": "Semiconductors", "symbol": "^YH31130020", "market weight": 0.382},
            {"name": "Software - Infrastructure", "symbol": "^YH31110030", "market weight": 0.211},
        ],
        index=pd.Index(["semiconductors", "software-infrastructure"], name="key"),
    )
    overview = {
        "companies_count": 858,
        "market_cap": 29_454_185_267_200,
        "market_weight": 0.334,
        "employee_count": 7_955_387,
        "description": "Companies engaged in the design of computer systems.",
    }
    frames = {
        "top_companies": top_companies,
        "top_etfs": None,
        "top_mutual_funds": None,
        "overview": overview,
        "industries": industries,
    }
    patch_run_sync(monkeypatch, _fetch_sector_blocking=frames)
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(
        Capability.SECTOR_OVERVIEW, sym, tls_policy=TLSPolicy.STRICT, sector_key="technology"
    )

    assert result.health.status is HealthStatus.OK
    stats = result.payload["stats"]
    assert stats["companies_count"] == 858  # type: ignore[index]
    assert stats["employee_count"] == 7_955_387  # type: ignore[index]
    industry_rows = result.payload["industries"]
    assert industry_rows[0]["industry_key"] == "semiconductors"  # type: ignore[index]
    assert industry_rows[0]["market_weight"] == pytest.approx(0.382)  # type: ignore[index]
    assert industry_rows[1]["industry_key"] == "software-infrastructure"  # type: ignore[index]


async def test_fetch_sector_overview_degrades_stats_and_industries_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real sector fetch (`top_companies` present) but a missing/malformed `overview`/
    `industries` frame still returns its company rows — `stats`/`industries` degrade
    independently, never blocking the whole result."""
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
    assert result.payload["stats"] is None
    assert result.payload["industries"] == []


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


async def test_fetch_industry_overview_maps_growth_and_performing_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    top_companies = pd.DataFrame(
        [{"name": "NVIDIA Corporation", "rating": "Strong Buy", "market weight": 0.44}],
        index=["NVDA"],
    )
    top_growth = pd.DataFrame(
        [{"name": "Micron Technology, Inc.", "ytd return": 2.6951, "growth estimate": 5.846}],
        index=["MU"],
    )
    top_performing = pd.DataFrame(
        [
            {
                "name": "MaxLinear, Inc.",
                "ytd return": 3.7656,
                "last price": 83.065,
                "target price": 94.5454,
            }
        ],
        index=["MXL"],
    )
    patch_run_sync(
        monkeypatch,
        _fetch_industry_blocking=(
            {
                "top_companies": top_companies,
                "top_growth_companies": top_growth,
                "top_performing_companies": top_performing,
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

    rows = result.payload["rows"]
    by_symbol = {row["symbol"]: row for row in rows}  # type: ignore[index]
    company_row = by_symbol["NVDA"]
    assert company_row["ytd_return"] is None  # top_companies never carries these four fields
    assert company_row["last_price"] is None

    growth_row = by_symbol["MU"]
    assert growth_row["row_kind"] == "growth_company"
    assert growth_row["ytd_return"] == pytest.approx(2.6951)
    assert growth_row["growth_estimate"] == pytest.approx(5.846)
    assert growth_row["last_price"] is None  # growth frame has no price/target columns

    performing_row = by_symbol["MXL"]
    assert performing_row["row_kind"] == "performing_company"
    assert performing_row["ytd_return"] == pytest.approx(3.7656)
    assert performing_row["last_price"] == pytest.approx(83.065)
    assert performing_row["target_price"] == pytest.approx(94.5454)
    assert performing_row["growth_estimate"] is None  # performing frame has no growth-estimate


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
