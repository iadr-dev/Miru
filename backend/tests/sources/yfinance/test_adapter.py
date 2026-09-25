"""Tests for `YFinanceAdapter.supports`/`fetch` dispatch itself — capability declaration,
unsupported-capability/market gating, and the generic exception-degrades-to-UNREACHABLE path.
Not tied to any one capability domain. Recorded fixtures, no live network.
"""

from __future__ import annotations

import anyio.to_thread
import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.yfinance import YFinanceAdapter


def test_supports_declares_three_capabilities_tw() -> None:
    adapter = YFinanceAdapter()
    supported = adapter.supports()
    assert (Capability.ANALYST_CONSENSUS, Market.TW) in supported
    assert (Capability.ESG, Market.TW) in supported
    assert (Capability.SECTOR_CLASS, Market.TW) in supported
    # TW must stay narrow — none of the non-TW-primary capabilities are declared for TW.
    assert (Capability.PRICE_EOD, Market.TW) not in supported
    assert (Capability.FINANCIALS, Market.TW) not in supported
    assert (Capability.OPTIONS_CHAIN, Market.TW) not in supported


def test_supports_declares_broad_set_for_non_tw_markets() -> None:
    adapter = YFinanceAdapter()
    supported = adapter.supports()
    for market in (Market.US, Market.GB, Market.JP, Market.EU):
        for cap in (
            Capability.PRICE_EOD,
            Capability.FINANCIALS,
            Capability.BALANCE_SHEET,
            Capability.CASH_FLOW_STATEMENT,
            Capability.DIVIDENDS,
            Capability.SPLIT_PRICE,
            Capability.ANALYST_CONSENSUS,
            Capability.ESG,
            Capability.SECTOR_CLASS,
            Capability.OPTIONS_CHAIN,
            Capability.INSTITUTIONAL_HOLDERS,
            Capability.INSIDER_ACTIVITY,
            Capability.SYMBOL_SEARCH,
            Capability.SYMBOL_LOOKUP,
            Capability.EARNINGS_CALENDAR,
            Capability.IPO_CALENDAR,
            Capability.ECONOMIC_CALENDAR,
            Capability.SECTOR_OVERVIEW,
            Capability.INDUSTRY_OVERVIEW,
            Capability.MARKET_STATUS,
            Capability.FUND_HOLDINGS,
            Capability.MARKET_VALUE,
            Capability.VALUATION_RATIOS,
            Capability.PROFILE,
        ):
            assert (cap, market) in supported


async def test_fetch_unsupported_capability() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_degrades_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    async def raise_run_sync(fn: object, *args: object) -> dict[str, object]:
        raise RuntimeError("simulated yfinance outage")

    monkeypatch.setattr(anyio.to_thread, "run_sync", raise_run_sync)
    adapter = YFinanceAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.ANALYST_CONSENSUS, sym, tls_policy=TLSPolicy.STRICT)

    # Never let a yfinance outage break a core flow — degrade via health, don't raise.
    assert result.health.status is HealthStatus.UNREACHABLE


async def test_fetch_market_status_unavailable_for_tw() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="2330", market=Market.TW)

    result = await adapter.fetch(Capability.MARKET_STATUS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
