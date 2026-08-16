"""`get_etf_overlap`/`get_fund_comparison` — driven through the real `dispatch_tool` path (per
this project's "prove it through the real dispatch path" discipline), plus a governance check
that neither tool's output can carry buy/sell/hold language.

`_MultiSymbolStubAdapter` (below) extends `tests.tools.conftest.StubAdapter`'s idea to answer
differently per requested symbol id — needed because both tools fan out over 2+ symbols with
genuinely different canned data per symbol (one degraded, others healthy).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent.guardrails import check_output
from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tools.base import ToolContext, dispatch_tool
from tools.common import SymbolInput
from tools.etf_overlap_tools import (
    GetEtfOverlapInput,
    GetEtfOverlapTool,
    GetFundComparisonInput,
    GetFundComparisonTool,
)
from tools.registry import ToolRegistry

_MARKETS = (Market.US, Market.TW)


class _MultiSymbolStubAdapter:
    """A stub `SourceAdapter` keyed by `(capability, symbol_id)` — answers differently per
    requested symbol, which the shared `tests.tools.conftest.StubAdapter` (keyed only by
    capability) can't express."""

    source_id = "STUB"
    timing_class = TimingClass.OFFICIAL_EOD

    def __init__(self, results: dict[tuple[Capability, str], SourceResult]) -> None:
        self._results = results
        self.calls: list[tuple[Capability, Symbol]] = []

    def supports(self) -> set[tuple[Capability, Market]]:
        caps = {cap for cap, _ in self._results}
        return {(cap, market) for cap in caps for market in _MARKETS}

    async def fetch(
        self, cap: Capability, sym: Symbol, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult:
        self.calls.append((cap, sym))
        key = (cap, sym.id)
        if key in self._results:
            return self._results[key]
        return SourceResult(
            source_id=self.source_id,
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.OFFICIAL_EOD,
            fetched_at=datetime.now(UTC),
            currentness_caveat="stub: no data configured for this symbol",
            payload={},
            health=SourceHealth(status=HealthStatus.STALE, detail="no stub data"),
        )


def _router(
    results: dict[tuple[Capability, str], SourceResult],
) -> tuple[DataAccessRouter, _MultiSymbolStubAdapter]:
    adapter = _MultiSymbolStubAdapter(results)
    caps = {cap for cap, _ in results}
    priority = {market.value: {cap.value: ["STUB"] for cap in caps} for market in _MARKETS}
    return DataAccessRouter({"STUB": adapter}, priority_config=priority), adapter


def _ok_result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _degraded_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={},
        health=SourceHealth(status=HealthStatus.STALE, detail="symbol is not a fund/ETF"),
    )


def _holding(symbol: str, holding_symbol: str, name: str, weight: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "US",
        "holding_symbol": holding_symbol,
        "holding_name": name,
        "weight": weight,
    }


def _price_bar(symbol: str, trade_date: date, close: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "US",
        "trade_date": trade_date.isoformat(),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
        "turnover": None,
    }


def _dividend_event(symbol: str, ex_date: date, cash: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "US",
        "announced_date": ex_date.isoformat(),
        "fiscal_year": str(ex_date.year),
        "cash_dividend": cash,
        "stock_dividend": None,
        "cash_ex_dividend_date": ex_date.isoformat(),
        "stock_ex_dividend_date": None,
    }


# ---- get_etf_overlap ----


async def test_get_etf_overlap_hand_verified_multi_way_and_pairwise_thresholds() -> None:
    """Three ETFs (FUND_A/FUND_B/FUND_C):
    - AAPL is held by all three (0.10 / 0.20 / 0.05) -> a genuine 3-way overlap row.
    - MSFT is held by FUND_A and FUND_B only (0.08 / 0.02) -> proves the "2+" threshold,
      not just "shared by all".
    - GOOG appears only in FUND_C -> never surfaces as an overlapping holding.

    Hand-computed pairwise overlap% (sum of min-weight-in-both over shared holdings):
    - FUND_A vs FUND_B: min(AAPL: .10,.20)=.10 + min(MSFT: .08,.02)=.02 = 0.12
    - FUND_A vs FUND_C: min(AAPL: .10,.05)=.05                          = 0.05
    - FUND_B vs FUND_C: min(AAPL: .20,.05)=.05                          = 0.05
    """
    results: dict[tuple[Capability, str], SourceResult] = {
        (Capability.FUND_HOLDINGS, "FUND_A"): _ok_result(
            {
                "holdings": [
                    _holding("FUND_A", "AAPL", "Apple Inc", 0.10),
                    _holding("FUND_A", "MSFT", "Microsoft Corp", 0.08),
                ]
            }
        ),
        (Capability.FUND_HOLDINGS, "FUND_B"): _ok_result(
            {
                "holdings": [
                    _holding("FUND_B", "AAPL", "Apple Inc", 0.20),
                    _holding("FUND_B", "MSFT", "Microsoft Corp", 0.02),
                ]
            }
        ),
        (Capability.FUND_HOLDINGS, "FUND_C"): _ok_result(
            {
                "holdings": [
                    _holding("FUND_C", "AAPL", "Apple Inc", 0.05),
                    _holding("FUND_C", "GOOG", "Alphabet Inc", 0.30),
                ]
            }
        ),
    }
    router, adapter = _router(results)
    tool = GetEtfOverlapTool(router)
    assert tool.data_class is DataClass.PUBLIC
    assert tool.card_type == "etf_overlap"

    registry = ToolRegistry()
    registry.register(tool)
    resolved_tool = registry.get_tool("get_etf_overlap")
    assert resolved_tool is not None

    output = await dispatch_tool(
        resolved_tool,
        GetEtfOverlapInput(symbols=("FUND_A", "FUND_B", "FUND_C"), market=Market.US),
        ToolContext(tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )

    assert not output.unavailable_symbols  # type: ignore[union-attr]
    by_holding = {row.holding_symbol: row for row in output.overlapping_holdings}  # type: ignore[union-attr]
    assert set(by_holding) == {"AAPL", "MSFT"}  # GOOG never appears — held by only one ETF
    assert set(by_holding["AAPL"].held_by) == {"FUND_A", "FUND_B", "FUND_C"}
    assert set(by_holding["MSFT"].held_by) == {"FUND_A", "FUND_B"}

    pairwise = {(r.etf_a, r.etf_b): r.overlap_pct for r in output.pairwise_overlap}  # type: ignore[union-attr]
    assert pairwise[("FUND_A", "FUND_B")] == pytest.approx(0.12)
    assert pairwise[("FUND_A", "FUND_C")] == pytest.approx(0.05)
    assert pairwise[("FUND_B", "FUND_C")] == pytest.approx(0.05)

    # 3 router calls made, one per requested symbol.
    assert len(adapter.calls) == 3


async def test_get_etf_overlap_reports_degraded_symbol_without_dropping_it_silently() -> None:
    """One of the three requested symbols has no usable holdings data — it must be named in
    `unavailable_symbols`, never silently dropped, while the other two still compute overlap."""
    results: dict[tuple[Capability, str], SourceResult] = {
        (Capability.FUND_HOLDINGS, "FUND_A"): _ok_result(
            {"holdings": [_holding("FUND_A", "AAPL", "Apple Inc", 0.10)]}
        ),
        (Capability.FUND_HOLDINGS, "FUND_B"): _ok_result(
            {"holdings": [_holding("FUND_B", "AAPL", "Apple Inc", 0.20)]}
        ),
        (Capability.FUND_HOLDINGS, "FUND_D"): _degraded_result(),
    }
    router, _ = _router(results)
    registry = ToolRegistry()
    registry.register(GetEtfOverlapTool(router))
    resolved_tool = registry.get_tool("get_etf_overlap")
    assert resolved_tool is not None

    output = await dispatch_tool(
        resolved_tool,
        GetEtfOverlapInput(symbols=("FUND_A", "FUND_B", "FUND_D"), market=Market.US),
        ToolContext(tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )

    assert output.unavailable_symbols == ("FUND_D",)  # type: ignore[union-attr]
    assert output.requested_symbols == ("FUND_A", "FUND_B", "FUND_D")  # type: ignore[union-attr]
    # The two healthy symbols still overlap on AAPL.
    assert output.overlapping_holdings[0].holding_symbol == "AAPL"  # type: ignore[union-attr]
    assert set(output.overlapping_holdings[0].held_by) == {"FUND_A", "FUND_B"}  # type: ignore[union-attr]


def test_get_etf_overlap_enforces_minimum_of_three_symbols() -> None:
    with pytest.raises(ValidationError):
        GetEtfOverlapInput(symbols=("FUND_A", "FUND_B"), market=Market.US)


def test_get_etf_overlap_rejects_tw_market() -> None:
    with pytest.raises(ValidationError):
        GetEtfOverlapInput(symbols=("0050", "00713", "00878"), market=Market.TW)


# ---- get_fund_comparison ----


async def test_get_fund_comparison_hand_computed_dividend_reinvestment() -> None:
    """Hand-computed reinvested total return for FUND_X:
    - Start close (day 1): 100.0 -> 1.0 share, cost basis 100.0.
    - Ex-dividend on day 2 at close 100.0: cash_dividend=5.0 -> buy 5.0/100.0 = 0.05 more shares.
      Shares after: 1.0 + 0.05*1.0 = 1.05.
    - End close (day 3): 110.0 -> ending value = 1.05 * 110.0 = 115.5.
    - Total return = (115.5 - 100.0) / 100.0 * 100 = 15.5%.
    """
    today = date.today()
    day1, day2, day3 = today - timedelta(days=2), today - timedelta(days=1), today
    results: dict[tuple[Capability, str], SourceResult] = {
        (Capability.PRICE_EOD, "FUND_X"): _ok_result(
            {
                "bars": [
                    _price_bar("FUND_X", day1, 100.0),
                    _price_bar("FUND_X", day2, 100.0),
                    _price_bar("FUND_X", day3, 110.0),
                ]
            }
        ),
        (Capability.DIVIDENDS, "FUND_X"): _ok_result(
            {"events": [_dividend_event("FUND_X", day2, 5.0)]}
        ),
        (Capability.FUND_HOLDINGS, "FUND_X"): _ok_result({"sector_weightings": []}),
        (Capability.PRICE_EOD, "FUND_Y"): _ok_result(
            {"bars": [_price_bar("FUND_Y", day1, 50.0), _price_bar("FUND_Y", day3, 55.0)]}
        ),
        (Capability.DIVIDENDS, "FUND_Y"): _ok_result({"events": []}),
        (Capability.FUND_HOLDINGS, "FUND_Y"): _ok_result({"sector_weightings": []}),
    }
    router, _ = _router(results)
    registry = ToolRegistry()
    registry.register(GetFundComparisonTool(router))
    resolved_tool = registry.get_tool("get_fund_comparison")
    assert resolved_tool is not None

    output = await dispatch_tool(
        resolved_tool,
        GetFundComparisonInput(
            symbols=(
                SymbolInput(id="FUND_X", market=Market.US),
                SymbolInput(id="FUND_Y", market=Market.US),
            ),
            lookback_days=30,
        ),
        ToolContext(tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )

    by_symbol = {row.symbol: row for row in output.rows}  # type: ignore[union-attr]
    assert by_symbol["FUND_X"].total_return_pct == pytest.approx(15.5)
    # FUND_Y: no dividends, plain price return (55-50)/50*100 = 10.0%.
    assert by_symbol["FUND_Y"].total_return_pct == pytest.approx(10.0)
    assert by_symbol["FUND_X"].unavailable_note is None  # non-TW: no TW-gap note
    assert by_symbol["FUND_X"].fund_info is None


async def test_get_fund_comparison_tw_symbol_honestly_omits_holdings_fields() -> None:
    """A TW symbol in the request must never get a fabricated overlap%/NAV figure — it gets an
    honest `unavailable_note` instead, and its basic-profile field is the TW fund-info slice,
    not sector weightings (which TW has no adapter for)."""
    today = date.today()
    day1, day2 = today - timedelta(days=4), today
    results: dict[tuple[Capability, str], SourceResult] = {
        (Capability.PRICE_EOD, "0050"): _ok_result(
            {"bars": [_price_bar("0050", day1, 140.0), _price_bar("0050", day2, 145.0)]}
        ),
        (Capability.DIVIDENDS, "0050"): _ok_result({"events": []}),
        (Capability.ETF_HOLDINGS, "0050"): _ok_result(
            {
                "info": {
                    "symbol": "0050",
                    "market": "TW",
                    "fund_name": "元大台灣50",
                    "fund_type": "被動式",
                    "tracked_index": "台灣50指數",
                    "units_outstanding": 1000,
                    "listed_date": "2003-06-30",
                    "manager_company": "元大投信",
                }
            }
        ),
        (Capability.PRICE_EOD, "AAPL"): _ok_result(
            {"bars": [_price_bar("AAPL", day1, 100.0), _price_bar("AAPL", day2, 105.0)]}
        ),
        (Capability.DIVIDENDS, "AAPL"): _ok_result({"events": []}),
        (Capability.FUND_HOLDINGS, "AAPL"): _ok_result({"sector_weightings": []}),
    }
    router, _ = _router(results)
    registry = ToolRegistry()
    registry.register(GetFundComparisonTool(router))
    resolved_tool = registry.get_tool("get_fund_comparison")
    assert resolved_tool is not None

    output = await dispatch_tool(
        resolved_tool,
        GetFundComparisonInput(
            symbols=(
                SymbolInput(id="0050", market=Market.TW),
                SymbolInput(id="AAPL", market=Market.US),
            ),
            lookback_days=30,
        ),
        ToolContext(tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )

    by_symbol = {row.symbol: row for row in output.rows}  # type: ignore[union-attr]
    tw_row = by_symbol["0050"]
    assert tw_row.unavailable_note is not None
    assert "holdings" in tw_row.unavailable_note.lower()
    assert tw_row.fund_info is not None
    assert tw_row.fund_info.fund_name == "元大台灣50"
    assert tw_row.sector_weightings == ()

    us_row = by_symbol["AAPL"]
    assert us_row.unavailable_note is None
    assert us_row.fund_info is None


def test_get_fund_comparison_enforces_minimum_of_two_symbols() -> None:
    with pytest.raises(ValidationError):
        GetFundComparisonInput(symbols=(SymbolInput(id="FUND_X", market=Market.US),))


# ---- governance: no trading language in either tool's output ----


async def test_get_etf_overlap_output_carries_no_trading_language() -> None:
    results: dict[tuple[Capability, str], SourceResult] = {
        (Capability.FUND_HOLDINGS, "FUND_A"): _ok_result(
            {"holdings": [_holding("FUND_A", "AAPL", "Apple Inc", 0.10)]}
        ),
        (Capability.FUND_HOLDINGS, "FUND_B"): _ok_result(
            {"holdings": [_holding("FUND_B", "AAPL", "Apple Inc", 0.20)]}
        ),
        (Capability.FUND_HOLDINGS, "FUND_C"): _ok_result(
            {"holdings": [_holding("FUND_C", "AAPL", "Apple Inc", 0.05)]}
        ),
    }
    router, _ = _router(results)
    registry = ToolRegistry()
    registry.register(GetEtfOverlapTool(router))
    resolved_tool = registry.get_tool("get_etf_overlap")
    assert resolved_tool is not None

    output = await dispatch_tool(
        resolved_tool,
        GetEtfOverlapInput(symbols=("FUND_A", "FUND_B", "FUND_C"), market=Market.US),
        ToolContext(tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )

    verdict = check_output(str(output.model_dump()))  # type: ignore[union-attr]
    assert verdict.allowed is True
    text = verdict.redacted_text or str(output.model_dump())  # type: ignore[union-attr]
    assert "[redacted" not in text


async def test_get_fund_comparison_output_carries_no_trading_language() -> None:
    today = date.today()
    day1, day2 = today - timedelta(days=4), today
    results: dict[tuple[Capability, str], SourceResult] = {
        (Capability.PRICE_EOD, "FUND_X"): _ok_result(
            {"bars": [_price_bar("FUND_X", day1, 100.0), _price_bar("FUND_X", day2, 110.0)]}
        ),
        (Capability.DIVIDENDS, "FUND_X"): _ok_result({"events": []}),
        (Capability.FUND_HOLDINGS, "FUND_X"): _ok_result({"sector_weightings": []}),
        (Capability.PRICE_EOD, "FUND_Y"): _ok_result(
            {"bars": [_price_bar("FUND_Y", day1, 50.0), _price_bar("FUND_Y", day2, 55.0)]}
        ),
        (Capability.DIVIDENDS, "FUND_Y"): _ok_result({"events": []}),
        (Capability.FUND_HOLDINGS, "FUND_Y"): _ok_result({"sector_weightings": []}),
    }
    router, _ = _router(results)
    registry = ToolRegistry()
    registry.register(GetFundComparisonTool(router))
    resolved_tool = registry.get_tool("get_fund_comparison")
    assert resolved_tool is not None

    output = await dispatch_tool(
        resolved_tool,
        GetFundComparisonInput(
            symbols=(
                SymbolInput(id="FUND_X", market=Market.US),
                SymbolInput(id="FUND_Y", market=Market.US),
            ),
            lookback_days=30,
        ),
        ToolContext(tier=Tier.FREE, tls_policy=TLSPolicy.STRICT),
    )

    verdict = check_output(str(output.model_dump()))  # type: ignore[union-attr]
    assert verdict.allowed is True
    text = verdict.redacted_text or str(output.model_dump())  # type: ignore[union-attr]
    assert "[redacted" not in text
