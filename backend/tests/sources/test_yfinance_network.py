"""Opt-in live yfinance verification — unofficial API, run manually. Never run in the default
suite.

uv run pytest -m network tests/sources/test_yfinance_network.py
"""

from __future__ import annotations

import pytest

from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.screener import (
    ScreenerAssetKind,
    ScreenerCondition,
    ScreenerOperator,
    ScreenerQuery,
)
from schemas.source import HealthStatus
from sources.yfinance import YFinanceAdapter, batch_download, run_screener

pytestmark = pytest.mark.network


async def test_live_analyst_consensus_2330_has_real_coverage() -> None:
    """2330 (TSMC) is mega-cap and does have real analyst coverage — this is the one symbol in
    this suite expected to come back with an actual consensus payload."""
    adapter = YFinanceAdapter()
    sym = Symbol(id="2330", market=Market.TW, yahoo="2330.TW")

    result = await adapter.fetch(Capability.ANALYST_CONSENSUS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    assert result.health.status is HealthStatus.OK
    consensus = result.payload["consensus"]
    assert consensus["number_of_analysts"] > 0  # type: ignore[index]


# Mid/small/micro-cap spread. This adapter's own module docstring documents analyst coverage as
# "sparse for mid/small-cap" and gated on `numberOfAnalystOpinions > 0` — these symbols MAY
# legitimately come back with zero analyst coverage. That is the expected, correct behavior
# this test proves: graceful degradation (the documented STALE "unavailable" shape), never a
# fabricated/empty-scaffolding "success".
_MID_CAP = "2385"  # Chicony Electronics
_SMALL_CAP = "8464"  # Nien Made
_MICRO_CAP_EMERGING = "4578"  # emerging-board (興櫃)


@pytest.mark.parametrize("stock_id", [_MID_CAP, _SMALL_CAP, _MICRO_CAP_EMERGING])
async def test_live_analyst_consensus_degrades_gracefully_when_sparse(stock_id: str) -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=stock_id, market=Market.TW, yahoo=f"{stock_id}.TW")

    result = await adapter.fetch(Capability.ANALYST_CONSENSUS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    if result.health.status is HealthStatus.OK:
        # If this symbol does happen to have real coverage, the payload must be real — never
        # fabricated/empty scaffolding masquerading as a consensus.
        consensus = result.payload["consensus"]
        assert consensus["number_of_analysts"] > 0  # type: ignore[index]
    else:
        # The documented "no analyst coverage" degradation — the expected outcome for sparse
        # mid/small/micro-cap coverage, not a test failure.
        assert result.health.status is HealthStatus.STALE
        assert result.payload == {}


# -- non-TW primary path: US is the priority market for proving "yfinance primary" works ---

_US_LARGE_CAP = "AAPL"
_US_MID_SMALL_CAP = "CHKP"  # Check Point Software — real mid-cap with real coverage
_GB_SYMBOL = "BP.L"
_JP_SYMBOL = "7203.T"  # Toyota


async def test_live_price_eod_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    assert result.health.status is HealthStatus.OK
    bars = result.payload["bars"]
    assert len(bars) > 0  # type: ignore[arg-type]
    # The most recent bar can be an in-progress/partial session (close still `None`) if the
    # market is open at fetch time — assert at least one *settled* bar has a real close rather
    # than assuming the very last row is always complete.
    assert any(bar["close"] is not None and bar["close"] > 0 for bar in bars)  # type: ignore[index,operator]


async def test_live_price_eod_gb() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_GB_SYMBOL, market=Market.GB)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    assert result.health.status is HealthStatus.OK
    bars = result.payload["bars"]
    assert len(bars) > 0  # type: ignore[arg-type]


async def test_live_price_eod_jp() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_JP_SYMBOL, market=Market.JP)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    assert result.health.status is HealthStatus.OK
    bars = result.payload["bars"]
    assert len(bars) > 0  # type: ignore[arg-type]


async def test_live_financials_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(Capability.FINANCIALS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    assert result.health.status is HealthStatus.OK
    assert len(result.payload["lines"]) > 0  # type: ignore[arg-type]


async def test_live_balance_sheet_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(Capability.BALANCE_SHEET, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    assert len(result.payload["lines"]) > 0  # type: ignore[arg-type]


async def test_live_cash_flow_statement_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(Capability.CASH_FLOW_STATEMENT, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    assert len(result.payload["lines"]) > 0  # type: ignore[arg-type]


async def test_live_dividends_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(Capability.DIVIDENDS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    assert len(result.payload["events"]) > 0  # type: ignore[arg-type]


async def test_live_analyst_consensus_extended_us_has_recommendations_tally() -> None:
    """AAPL is mega-cap US, so the non-TW extended analyst fields should populate for real —
    proves yfinance's primary-for-non-TW analyst path, not just the narrow TW gap-filler."""
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(Capability.ANALYST_CONSENSUS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    consensus = result.payload["consensus"]
    assert consensus["number_of_analysts"] > 0  # type: ignore[index]
    # At least one of the extended fields should be present for a mega-cap — not asserting all,
    # since Yahoo's own upstream availability of any one sub-frame can vary day to day.
    assert any(
        consensus.get(key) is not None  # type: ignore[union-attr]
        for key in ("strong_buy", "forward_eps_estimate", "long_term_growth_estimate")
    )


async def test_live_analyst_consensus_us_mid_cap() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_MID_SMALL_CAP, market=Market.US)

    result = await adapter.fetch(Capability.ANALYST_CONSENSUS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    if result.health.status is HealthStatus.OK:
        consensus = result.payload["consensus"]
        assert consensus["number_of_analysts"] > 0  # type: ignore[index]
    else:
        assert result.health.status is HealthStatus.STALE
        assert result.payload == {}


async def test_live_esg_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(Capability.ESG, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    # ESG coverage is genuinely sparse even for large caps (Yahoo has stopped serving
    # sustainability for many symbols) — graceful degradation is a legitimate outcome here.
    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)


async def test_live_sector_class_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(Capability.SECTOR_CLASS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    assert result.payload["classification"]["sector"]  # type: ignore[index]


async def test_live_options_chain_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(Capability.OPTIONS_CHAIN, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    assert result.health.status is HealthStatus.OK
    assert len(result.payload["contracts"]) > 0  # type: ignore[arg-type]


async def test_live_institutional_holders_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(Capability.INSTITUTIONAL_HOLDERS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    assert len(result.payload["holders"]) > 0  # type: ignore[arg-type]


async def test_live_insider_activity_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(Capability.INSIDER_ACTIVITY, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    # Insider disclosure churns unpredictably even for AAPL — degrade gracefully rather than
    # assert a fixed non-empty result.
    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)


async def test_live_fund_holdings_us_etf() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="QQQ", market=Market.US)

    result = await adapter.fetch(Capability.FUND_HOLDINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    assert len(result.payload["holdings"]) > 0  # type: ignore[arg-type]


async def test_live_symbol_search_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(
        Capability.SYMBOL_SEARCH, sym, tls_policy=TLSPolicy.STRICT, query="Apple"
    )

    assert result.health.status is HealthStatus.OK
    results = result.payload["results"]
    assert any(r["symbol"] == "AAPL" for r in results)  # type: ignore[union-attr]


async def test_live_symbol_lookup_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id=_US_LARGE_CAP, market=Market.US)

    result = await adapter.fetch(Capability.SYMBOL_LOOKUP, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    assert len(result.payload["results"]) > 0  # type: ignore[arg-type]


async def test_live_earnings_calendar_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(Capability.EARNINGS_CALENDAR, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    # A calendar window can legitimately be empty at fetch time (no defaults were passed);
    # both outcomes are valid, real behavior.
    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)


async def test_live_ipo_calendar_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(Capability.IPO_CALENDAR, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)


async def test_live_economic_calendar() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(Capability.ECONOMIC_CALENDAR, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)


async def test_live_sector_overview_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(
        Capability.SECTOR_OVERVIEW, sym, tls_policy=TLSPolicy.STRICT, sector_key="technology"
    )

    assert result.health.status is HealthStatus.OK
    assert len(result.payload["rows"]) > 0  # type: ignore[arg-type]


async def test_live_industry_overview_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(
        Capability.INDUSTRY_OVERVIEW,
        sym,
        tls_policy=TLSPolicy.STRICT,
        industry_key="semiconductors",
    )

    assert result.health.status is HealthStatus.OK
    assert len(result.payload["rows"]) > 0  # type: ignore[arg-type]


async def test_live_market_status_us() -> None:
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.US)

    result = await adapter.fetch(Capability.MARKET_STATUS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    assert result.payload["status"]["status"]  # type: ignore[index]


async def test_live_market_status_gb_degrades_gracefully() -> None:
    """Discovered live behavior: Yahoo's underlying markettime endpoint only actually serves
    `US` — GB/ASIA/EUROPE come back empty. This proves the coverage gate degrades honestly
    (`STALE`, not a fabricated status) rather than asserting a fixed non-empty result."""
    adapter = YFinanceAdapter()
    sym = Symbol(id="", market=Market.GB)

    result = await adapter.fetch(Capability.MARKET_STATUS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.currentness_caveat
    assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)


async def test_live_batch_download_us() -> None:
    frame = await batch_download(["AAPL", "MSFT"], start="2026-07-01", end="2026-08-01")

    assert frame is not None
    assert not frame.empty


# -- screener: query-shaped carve-out, run against the real Yahoo screener endpoint ------------


async def test_live_equity_screener_us_large_cap_tech_under_pe_30() -> None:
    """Real `EquityQuery` composition, real network call — US large-cap tech, P/E under 30."""
    query = ScreenerQuery(
        asset_kind=ScreenerAssetKind.EQUITY,
        conditions=(
            ScreenerCondition(
                field="intradaymarketcap", op=ScreenerOperator.GT, value=10_000_000_000
            ),
            ScreenerCondition(field="peratio.lasttwelvemonths", op=ScreenerOperator.LT, value=30),
            ScreenerCondition(field="sector", op=ScreenerOperator.EQ, value="Technology"),
            ScreenerCondition(field="region", op=ScreenerOperator.EQ, value="us"),
        ),
        size=10,
        sort_field="intradaymarketcap",
        sort_ascending=False,
    )

    result = await run_screener(query)

    assert result.detail == "ok"
    assert result.total_matches > 0
    assert len(result.hits) > 0
    assert all(hit.symbol for hit in result.hits)


async def test_live_etf_screener_large_net_assets() -> None:
    """Real `ETFQuery`, real network call — ETFs with fund net assets over $1B."""
    query = ScreenerQuery(
        asset_kind=ScreenerAssetKind.ETF,
        conditions=(
            ScreenerCondition(field="fundnetassets", op=ScreenerOperator.GT, value=1_000_000_000),
        ),
        size=10,
    )

    result = await run_screener(query)

    assert result.detail == "ok"
    assert result.total_matches > 0
    assert len(result.hits) > 0
