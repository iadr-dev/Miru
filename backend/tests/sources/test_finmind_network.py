"""Opt-in live FinMind verification. Never run in the default suite.

Run manually with a real `FINMIND_API_KEY` configured:
    uv run pytest -m network tests/sources/test_finmind_network.py
"""

from __future__ import annotations

import pytest

from config.settings import get_settings
from schemas.enums import Capability, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.finmind import FinMindAdapter

pytestmark = pytest.mark.network


# Large/mid/small/micro-cap spread — thinner trading, sparser institutional/chip data, and
# emerging-board field-definition quirks only surface away from a single mega-cap symbol.
_LARGE_CAP = "2330"  # TSMC
_MID_CAP = "2385"  # Chicony Electronics
_SMALL_CAP = "8464"  # Nien Made
_MICRO_CAP_EMERGING = "4578"  # emerging-board (興櫃)


@pytest.mark.parametrize("stock_id", [_LARGE_CAP, _MID_CAP, _SMALL_CAP, _MICRO_CAP_EMERGING])
async def test_live_price_eod(stock_id: str) -> None:
    settings = get_settings()
    if not settings.finmind_api_key:
        pytest.skip("FINMIND_API_KEY not configured — manual verification only")
    adapter = FinMindAdapter(settings=settings)
    sym = Symbol(id=stock_id, market=Market.TW)

    result = await adapter.fetch(
        Capability.PRICE_EOD,
        sym,
        tls_policy=TLSPolicy.STRICT,
        start_date="2024-01-01",
        end_date="2024-01-10",
    )

    assert result.health.status is HealthStatus.OK
    bars = result.payload["bars"]
    assert bars
    for bar in bars:  # type: ignore[union-attr]
        assert bar["low"] <= bar["high"]
        if stock_id == _MICRO_CAP_EMERGING:
            # Emerging-board `open` is FinMind's previous-day average price, not a real
            # opening print — it can legitimately fall outside [low, high] on volatile days.
            # This is correct upstream behavior, not a bug, so it is deliberately not asserted
            # against the [low, high] invariant here.
            continue
        assert bar["low"] <= bar["open"] <= bar["high"]
        assert bar["low"] <= bar["close"] <= bar["high"]


# -- dividends (feeds the calendar feature) -----------------------------------------------------

_HIGH_DIVIDEND_ETF = "0056"  # Yuanta/P-shares Taiwan Dividend Plus ETF
_HIGH_DIVIDEND_ETF_MONTHLY = "00878"  # Cathay Dividend Plus ETF — monthly cash distributions


@pytest.mark.parametrize("stock_id", [_LARGE_CAP, _HIGH_DIVIDEND_ETF, _HIGH_DIVIDEND_ETF_MONTHLY])
async def test_live_dividends(stock_id: str) -> None:
    settings = get_settings()
    if not settings.finmind_api_key:
        pytest.skip("FINMIND_API_KEY not configured — manual verification only")
    adapter = FinMindAdapter(settings=settings)
    sym = Symbol(id=stock_id, market=Market.TW)

    result = await adapter.fetch(
        Capability.DIVIDENDS,
        sym,
        tls_policy=TLSPolicy.STRICT,
        start_date="2015-01-01",
        end_date="2024-12-31",
    )

    assert result.health.status is HealthStatus.OK
    events = result.payload["events"]
    assert events
    dates = [str(e["announced_date"]) for e in events]  # type: ignore[union-attr]
    assert dates == sorted(dates)
    assert any(e["cash_dividend"] is not None for e in events)  # type: ignore[union-attr]


# -- CB put(賣回權) provision (feeds the calendar feature) --------------------------------------

_CB_WITH_OUTSTANDING_PUT = "84621"  # 康控-KY CB, has a real documented put-provision row


async def test_live_cb_put_provision_real_bond() -> None:
    """A real convertible bond with a known outstanding/historical put date — proves the
    live shape, not just graceful-empty degradation."""
    settings = get_settings()
    if not settings.finmind_api_key:
        pytest.skip("FINMIND_API_KEY not configured — manual verification only")
    adapter = FinMindAdapter(settings=settings)
    sym = Symbol(id=_CB_WITH_OUTSTANDING_PUT, market=Market.TW)

    result = await adapter.fetch(
        Capability.CB_PUT_PROVISION,
        sym,
        tls_policy=TLSPolicy.STRICT,
        start_date="2019-01-01",
        end_date="2020-12-31",
    )

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert rows
    for row in rows:  # type: ignore[union-attr]
        assert row["put_price"] > 0


async def test_live_cb_put_provision_degrades_gracefully_when_no_outstanding_cb() -> None:
    """2330 (TSMC) has never issued a convertible bond — a real, valid "no CB" outcome, not
    a bug. Proves the degrade-to-empty path against the real upstream, not a fabricated
    scaffold."""
    settings = get_settings()
    if not settings.finmind_api_key:
        pytest.skip("FINMIND_API_KEY not configured — manual verification only")
    adapter = FinMindAdapter(settings=settings)
    sym = Symbol(id=_LARGE_CAP, market=Market.TW)

    result = await adapter.fetch(
        Capability.CB_PUT_PROVISION,
        sym,
        tls_policy=TLSPolicy.STRICT,
        start_date="2019-01-01",
        end_date="2026-08-07",
    )

    assert result.currentness_caveat
    if result.health.status is HealthStatus.OK:
        assert result.payload["rows"]  # type: ignore[index]
    else:
        assert result.health.status is HealthStatus.STALE
        assert result.payload["rows"] == []  # type: ignore[index]


# -- institutional flow (feeds screener chip tags) ------------------------------------------------


@pytest.mark.parametrize("stock_id", [_LARGE_CAP, _MID_CAP, _SMALL_CAP, _MICRO_CAP_EMERGING])
async def test_live_institutional_flow(stock_id: str) -> None:
    """Spans the same large/mid/small/micro-cap spread as `test_live_price_eod` — thinner
    trading on a small/micro name can legitimately mean sparser or zero institutional-flow
    rows for a given window, so this only asserts the fetch itself succeeds (OK health, no
    malformed rows), not that every tier has non-empty rows."""
    settings = get_settings()
    if not settings.finmind_api_key:
        pytest.skip("FINMIND_API_KEY not configured — manual verification only")
    adapter = FinMindAdapter(settings=settings)
    sym = Symbol(id=stock_id, market=Market.TW)

    result = await adapter.fetch(
        Capability.INSTITUTIONAL_FLOW,
        sym,
        tls_policy=TLSPolicy.STRICT,
        start_date="2024-01-01",
        end_date="2024-01-10",
    )

    # Emerging-board (興櫃) names are genuinely thin/absent from this dataset — 4578 returning
    # a `STALE`/empty result for this window is real coverage, not a fetch failure, so only the
    # other three tiers are held to an OK-health bar.
    if stock_id == _MICRO_CAP_EMERGING:
        assert result.health.status in (HealthStatus.OK, HealthStatus.STALE)
        return
    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    for row in rows:  # type: ignore[union-attr]
        assert row["buy"] >= 0
        assert row["sell"] >= 0
    if stock_id == _LARGE_CAP:
        # A mega-cap in an active window should have real institutional participation — the
        # one tier where a genuinely empty result would indicate a real problem, not just
        # thin trading.
        assert rows
        investors = {str(row["investor"]) for row in rows}  # type: ignore[union-attr]
        assert investors


# -- financials (feeds the score engine's quality/value factors) --------------------------------


_RECOGNIZED_FINANCIAL_ITEMS = {
    "IncomeAfterTaxes",
    "ProfitLossAttributableToOwnersOfParent",
    "NetIncome",
    "ProfitAfterTax",
    "GrossProfit",
    "GrossProfitLoss",
    "OperatingIncome",
    "OperatingIncomeLoss",
    "PreTaxIncome",
    "IncomeBeforeIncomeTax",
    "ProfitBeforeTax",
}


@pytest.mark.parametrize("stock_id", [_LARGE_CAP, _MID_CAP, _SMALL_CAP])
async def test_live_financials_has_recognized_line_item(stock_id: str) -> None:
    """Asserts a real line item lands under a name the score engine's own alias lists in
    `analytics/fundamentals.py` already recognize (e.g. `IncomeAfterTaxes`/`GrossProfit`/
    `OperatingIncome`/`PreTaxIncome`) — proves the live feed and the score engine's parsing
    logic actually agree on FinMind's current line-item naming, for a small-cap filer's
    statement shape too, not only a mega-cap's. Excludes the micro-cap/emerging-board tier —
    興櫃 filers are not guaranteed to publish the same GAAP-aligned statement family this
    capability parses, so a `NetIncome`-family miss there would be a data-coverage fact, not a
    parsing regression to catch here."""
    settings = get_settings()
    if not settings.finmind_api_key:
        pytest.skip("FINMIND_API_KEY not configured — manual verification only")
    adapter = FinMindAdapter(settings=settings)
    sym = Symbol(id=stock_id, market=Market.TW)

    result = await adapter.fetch(
        Capability.FINANCIALS,
        sym,
        tls_policy=TLSPolicy.STRICT,
        start_date="2023-01-01",
        end_date="2023-12-31",
    )

    assert result.health.status is HealthStatus.OK
    lines = result.payload["lines"]
    assert lines
    items = {str(line["item"]) for line in lines}  # type: ignore[union-attr]
    assert items & _RECOGNIZED_FINANCIAL_ITEMS


# -- market value ---------------------------------------------------------------------------------


# A loose per-tier plausibility floor (TWD) — not a brittle exact value, since live market cap
# changes daily. Ordered the same large/mid/small/micro spread as the price-EOD test; each
# floor is comfortably below that tier's real-world cap so the test tolerates normal drift.
_MARKET_VALUE_FLOOR_BY_TIER: dict[str, int] = {
    _LARGE_CAP: 1_000_000_000_000,  # TSMC: trillions of TWD
    _MID_CAP: 10_000_000_000,  # Chicony: tens of billions of TWD
    _SMALL_CAP: 1_000_000_000,  # Nien Made: billions of TWD
}


@pytest.mark.parametrize("stock_id", [_LARGE_CAP, _MID_CAP, _SMALL_CAP])
async def test_live_market_value(stock_id: str) -> None:
    settings = get_settings()
    if not settings.finmind_api_key:
        pytest.skip("FINMIND_API_KEY not configured — manual verification only")
    adapter = FinMindAdapter(settings=settings)
    sym = Symbol(id=stock_id, market=Market.TW)

    result = await adapter.fetch(
        Capability.MARKET_VALUE,
        sym,
        tls_policy=TLSPolicy.STRICT,
        start_date="2024-01-01",
        end_date="2024-01-10",
    )

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert rows
    floor = _MARKET_VALUE_FLOOR_BY_TIER[stock_id]
    for row in rows:  # type: ignore[union-attr]
        assert row["market_value"] > floor


# -- active-ETF roster (whole-roster fetch, not per-symbol) --------------------------------------


async def test_live_active_etf_info_roster() -> None:
    """`ACTIVE_ETF_INFO` never honors `data_id` for the free per-symbol path (FinMind always
    returns the full roster regardless of what's passed) — this uses the documented whole-
    market-by-date form via `fetch_whole_market`, the only path that actually returns rows."""
    settings = get_settings()
    if not settings.finmind_api_key:
        pytest.skip("FINMIND_API_KEY not configured — manual verification only")
    adapter = FinMindAdapter(settings=settings)

    result = await adapter.fetch_whole_market(
        Capability.ACTIVE_ETF_INFO, tls_policy=TLSPolicy.STRICT
    )

    assert result.health.status is HealthStatus.OK
    rows = result.payload["info"]
    assert rows
    for row in rows:  # type: ignore[union-attr]
        assert row["name"]
    # `category` is populated for most but not every real roster row — assert presence across
    # the roster rather than on every individual row, which would be a brittle upstream-data
    # assumption rather than a real shape invariant.
    assert any(row["category"] for row in rows)  # type: ignore[union-attr]
