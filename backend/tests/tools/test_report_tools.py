"""The report tools over a stub router: the table is computed once in code, charts are declared, and
nothing is invented when inputs are missing."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import ClassVar

import pytest

from dataaccess.router import DataAccessRouter
from schemas.annual_report import EtfAnnualRow
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import StubAdapter, make_router
from tools.base import ToolContext
from tools.report_tools import (
    ComparisonInput,
    GetAnnualFundamentalsOutput,
    GetAnnualFundamentalsTool,
    GetComparisonMetricsOutput,
    GetComparisonMetricsTool,
    GetRevenueSeasonalityOutput,
    GetRevenueSeasonalityTool,
    GetRiskMetricsOutput,
    GetRiskMetricsTool,
    RiskMetrics,
    _RiskBundle,
    _Side,
    _stability_row,
    _YearsInput,
)

_SYMBOL = Symbol(id="2330", market=Market.TW)
_THIS_YEAR = date.today().year


def _result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _lines(statement: str, year: int, items: dict[str, float]) -> list[dict[str, object]]:
    return [
        {
            "symbol": "2330",
            "market": "TW",
            "period_end": f"{year}-12-31",
            "statement_type": statement,
            "period_type": "quarterly",
            "item": item,
            "origin_name": item,
            "value": value,
        }
        for item, value in items.items()
    ]


def _annual_payloads(years: list[int]) -> dict[Capability, SourceResult]:
    income: list[dict[str, object]] = []
    balance: list[dict[str, object]] = []
    for i, year in enumerate(years):
        # Four identical quarters would need four period_ends; one Q4 line per year is enough for
        # the balance sheet, and income gets all four quarters.
        for quarter_end in (f"{year}-03-31", f"{year}-06-30", f"{year}-09-30", f"{year}-12-31"):
            for item, value in {
                "Revenue": 1e10 * (1 + i),
                "GrossProfit": 5e9 * (1 + i),
                "OperatingIncome": 3e9 * (1 + i),
                "EquityAttributableToOwnersOfParent": 2.5e9 * (1 + i),
                "EPS": 2.0,
            }.items():
                income.append(
                    {
                        "symbol": "2330",
                        "market": "TW",
                        "period_end": quarter_end,
                        "statement_type": "income",
                        "item": item,
                        "origin_name": item,
                        "value": value,
                    }
                )
        balance += _lines(
            "balance_sheet",
            year,
            {
                "EquityAttributableToOwnersOfParent": 1e11,
                "Liabilities": 4e10,
                "TotalAssets": 1e11,
                "CurrentAssets": 6e10,
                "CurrentLiabilities": 3e10,
            },
        )
    bars = [
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": f"{year}-12-30",
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 100.0,
            "volume": 1,
        }
        for year in years
    ]
    events = [
        {
            "symbol": "2330",
            "market": "TW",
            "announced_date": f"{year + 1}-02-01",
            "fiscal_year": f"{year - 1911}年",
            "cash_dividend": 5.0,
            "stock_dividend": 0.0,
        }
        for year in years
    ]
    return {
        Capability.FINANCIALS: _result({"lines": income}),
        Capability.BALANCE_SHEET: _result({"lines": balance}),
        Capability.CASH_FLOW_STATEMENT: _result({"lines": []}),
        Capability.DIVIDENDS: _result({"events": events}),
        Capability.PRICE_EOD: _result({"bars": bars}),
    }


async def test_annual_tool_returns_scaled_rows_and_one_chart_per_series() -> None:
    years = [_THIS_YEAR - 3, _THIS_YEAR - 2, _THIS_YEAR - 1]
    router, _ = make_router(_SYMBOL, _annual_payloads(years))
    tool = GetAnnualFundamentalsTool(router)

    out = await tool.run(_YearsInput(id="2330", market=Market.TW, years=5), ToolContext())

    assert isinstance(out, GetAnnualFundamentalsOutput)
    assert out.amount_unit.startswith("億元")
    latest = out.rows[-1]
    assert latest.revenue == pytest.approx(1200.0)  # 4 quarters x 3e10 = 1.2e11 NT$ = 1,200 億
    assert latest.gross_margin_pct == pytest.approx(50.0)
    assert latest.debt_ratio_pct == pytest.approx(40.0)
    assert latest.current_ratio == pytest.approx(2.0)
    assert latest.cash_dividend == 5.0
    assert out.coverage_note is not None  # only 3 of the 5 requested years exist
    titles = [hint.title for hint in out.chart_hints]
    assert any("營收" in (t or "") for t in titles)
    assert len(out.chart_hints) == 5
    # every hint points at a series that actually has points
    assert all(getattr(out, hint.data_key) for hint in out.chart_hints if hint.data_key)


async def test_annual_tool_without_data_returns_no_charts_instead_of_empty_ones() -> None:
    router, _ = make_router(_SYMBOL, {Capability.FINANCIALS: _result({"lines": []})})
    tool = GetAnnualFundamentalsTool(router)

    out = await tool.run(_YearsInput(id="2330", market=Market.TW), ToolContext())

    assert isinstance(out, GetAnnualFundamentalsOutput)
    assert out.rows == ()
    assert out.chart_hints == ()


def _month_rows(years: list[int]) -> list[dict[str, object]]:
    shape = [80, 80, 90, 95, 100, 100, 105, 110, 115, 120, 125, 180]
    return [
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": f"{year}-{month:02d}-10",
            "country": "Taiwan",
            "revenue": value * (1 + i),
            "revenue_month": month,
            "revenue_year": year,
        }
        for i, year in enumerate(years)
        for month, value in enumerate(shape, start=1)
    ]


async def test_seasonality_tool_reports_peak_and_a_bar_chart() -> None:
    router, _ = make_router(
        _SYMBOL,
        {Capability.MONTH_REVENUE: _result({"rows": _month_rows([2022, 2023, 2024])})},
    )
    tool = GetRevenueSeasonalityTool(router)

    out = await tool.run(_YearsInput(id="2330", market=Market.TW), ToolContext())

    assert isinstance(out, GetRevenueSeasonalityOutput)
    assert out.summary is not None
    assert out.summary.peak_quarter == 4
    assert len(out.periods) == 12
    assert out.chart_hint is not None
    assert out.chart_hint.data_key == "periods"


async def test_seasonality_tool_says_why_when_history_is_too_short() -> None:
    router, _ = make_router(
        _SYMBOL, {Capability.MONTH_REVENUE: _result({"rows": _month_rows([2024])})}
    )

    out = await GetRevenueSeasonalityTool(router).run(
        _YearsInput(id="2330", market=Market.TW), ToolContext()
    )

    assert isinstance(out, GetRevenueSeasonalityOutput)
    assert out.summary is None
    assert out.unavailable_reason is not None
    assert out.chart_hint is None


def _bars(n_days: int, growth: float) -> list[dict[str, object]]:
    start = date.today() - timedelta(days=n_days)
    close = 100.0
    out: list[dict[str, object]] = []
    for i in range(n_days):
        close *= 1 + growth * (1 if i % 3 else -1.5)
        out.append(
            {
                "symbol": "2330",
                "market": "TW",
                "trade_date": (start + timedelta(days=i)).isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1,
            }
        )
    return out


async def test_risk_tool_computes_beta_against_the_proxy_and_names_it() -> None:
    router, _ = make_router(_SYMBOL, {Capability.PRICE_EOD: _result({"bars": _bars(400, 0.01)})})

    out = await GetRiskMetricsTool(router).run(
        _YearsInput(id="2330", market=Market.TW), ToolContext()
    )

    assert isinstance(out, GetRiskMetricsOutput)
    assert out.metrics.beta == pytest.approx(1.0, abs=0.01)  # the stub serves one series to both
    assert out.metrics.max_drawdown_pct is not None
    assert out.metrics.max_drawdown_pct <= 0
    assert "0050" in out.benchmark
    assert out.chart_hint is not None
    assert out.price_series


async def test_risk_tool_with_no_prices_is_honest() -> None:
    router, _ = make_router(_SYMBOL, {Capability.PRICE_EOD: _result({"bars": []})})

    out = await GetRiskMetricsTool(router).run(
        _YearsInput(id="2330", market=Market.TW), ToolContext()
    )

    assert isinstance(out, GetRiskMetricsOutput)
    assert out.unavailable_reason is not None
    assert out.metrics.beta is None
    assert out.chart_hint is None


async def test_comparison_tool_decides_the_stronger_side_by_code_with_a_stated_rule() -> None:
    years = [_THIS_YEAR - 3, _THIS_YEAR - 2, _THIS_YEAR - 1]
    payloads = _annual_payloads(years)
    payloads[Capability.PRICE_EOD] = _result({"bars": _bars(400, 0.01)})
    payloads[Capability.MONTH_REVENUE] = _result({"rows": _month_rows([2022, 2023, 2024])})
    router, _ = make_router(_SYMBOL, payloads)

    out = await GetComparisonMetricsTool(router).run(
        ComparisonInput(id_a="2330", market_a=Market.TW, id_b="2317", market_b=Market.TW),
        ToolContext(),
    )

    assert isinstance(out, GetComparisonMetricsOutput)
    by_metric = {row.metric: row for row in out.rows}
    # The stub serves identical data to both sides: every comparable metric ties, and a non-numeric
    # one (seasonality text) carries no verdict at all.
    assert by_metric["平均 ROE"].stronger == "tie"
    assert by_metric["季節性波動"].stronger is None
    assert by_metric["波動度 (Beta)"].direction == "越低越佳"
    assert by_metric["營收 CAGR"].direction == "越高越佳"
    assert any("非買賣建議" in note or "買賣建議" in note for note in out.notes)
    assert out.chart_hint is not None
    assert out.chart_hint.color is not None


# ---- ETF comparison -------------------------------------------------------------------------


class _EtfAdapter(StubAdapter):
    """Serves one ETF-shaped payload per capability, with a per-symbol expense ratio so the two
    sides differ (a stub that answered both identically could only ever produce ties)."""

    _EXPENSE: ClassVar[dict[str, float]] = {"0050": 0.4, "00878": 0.7}

    async def fetch(self, cap, sym, *, tls_policy, **params):  # type: ignore[no-untyped-def]
        self.calls.append((cap, sym, params))
        if cap is Capability.ETF_EXPENSE_RATIO:
            return _result(
                {
                    "info": {
                        "symbol": sym.id,
                        "market": "TW",
                        "total_expense_ratio_pct_approx": self._EXPENSE.get(sym.id),
                    }
                }
            )
        if cap is Capability.ETF_HOLDINGS:
            return _result(
                {
                    "info": {
                        "symbol": sym.id,
                        "market": "TW",
                        "fund_name": sym.id,
                        "fund_type": "ETF",
                        "tracked_index": f"{sym.id} index",
                    }
                }
            )
        if cap is Capability.ETF_TRAILING_RETURNS:
            return _result(
                {
                    "returns": {
                        "symbol": sym.id,
                        "market": "TW",
                        "as_of": "2026-10-01",
                        "return_1y_pct": 30.0 if sym.id == "0050" else 12.0,
                        "return_3y_pct": 90.0,
                    }
                }
            )
        return self._results.get(cap, _result({}))


def _etf_router(symbol_market: Market = Market.TW):
    caps = {
        Capability.PRICE_EOD,
        Capability.DIVIDENDS,
        Capability.ETF_EXPENSE_RATIO,
        Capability.ETF_HOLDINGS,
        Capability.ETF_TRAILING_RETURNS,
        Capability.FINANCIALS,
        Capability.BALANCE_SHEET,
        Capability.CASH_FLOW_STATEMENT,
        Capability.MONTH_REVENUE,
    }
    adapter = _EtfAdapter(
        "STUB",
        supported={(cap, symbol_market) for cap in caps},
        results={
            Capability.PRICE_EOD: _result({"bars": _bars(900, 0.004)}),
            Capability.DIVIDENDS: _result(
                {
                    "events": [
                        {
                            "symbol": "x",
                            "market": "TW",
                            "announced_date": f"{date.today().year - 1}-07-15",
                            "fiscal_year": "x",
                            "cash_dividend": 1.0,
                            "cash_ex_dividend_date": f"{date.today().year - 1}-07-15",
                        }
                    ]
                }
            ),
        },
    )
    priority = {symbol_market.value: {cap.value: ["STUB"] for cap in caps}}
    return DataAccessRouter({"STUB": adapter}, priority_config=priority)


async def test_two_etfs_get_etf_metrics_not_the_stock_rows() -> None:
    out = await GetComparisonMetricsTool(_etf_router()).run(
        ComparisonInput(id_a="0050", market_a=Market.TW, id_b="00878", market_b=Market.TW),
        ToolContext(),
    )

    assert isinstance(out, GetComparisonMetricsOutput)
    assert (out.kind_a, out.kind_b) == ("etf", "etf")
    metrics = {row.metric: row for row in out.rows}
    for stock_only in ("營收 CAGR", "EPS 成長率（CAGR）", "平均 ROE", "淡旺季營收差異"):
        assert stock_only not in metrics  # an ETF has no revenue, EPS or ROE
    assert not any("負債比" in name for name in metrics)
    assert "費用率（經理費與保管費，下限近似）" in metrics
    assert "含息年化報酬（概估）" in metrics
    assert "近 1 年累計報酬" in metrics
    expense = metrics["費用率（經理費與保管費，下限近似）"]
    assert (expense.a, expense.b) == ("0.40%", "0.70%")
    assert expense.stronger == "A"
    assert expense.direction == "越低越佳"
    assert metrics["近 1 年累計報酬"].stronger == "A"  # 30% vs 12%
    assert metrics["追蹤指數、類別"].stronger is None  # described, never judged
    assert any("ETF 沒有營收" in note for note in out.notes)


async def test_a_stock_against_an_etf_shares_only_the_common_metrics() -> None:
    out = await GetComparisonMetricsTool(_etf_router()).run(
        ComparisonInput(id_a="0050", market_a=Market.TW, id_b="2330", market_b=Market.TW),
        ToolContext(),
    )

    assert isinstance(out, GetComparisonMetricsOutput)
    assert (out.kind_a, out.kind_b) == ("etf", "stock")
    names = [row.metric for row in out.rows]
    assert names[:2] == ["平均殖利率", "配息穩定性"]
    assert "營收 CAGR" not in names
    assert "費用率（經理費與保管費，下限近似）" not in names
    assert any("只比較兩者共有的指標" in note for note in out.notes)


async def test_etf_comparison_shows_the_disclosed_part_of_the_fee_and_breaks_stability_ties() -> (
    None
):
    out = await GetComparisonMetricsTool(_etf_router()).run(
        ComparisonInput(id_a="0050", market_a=Market.TW, id_b="00878", market_b=Market.TW),
        ToolContext(),
    )

    assert isinstance(out, GetComparisonMetricsOutput)
    by_metric = {row.metric: row for row in out.rows}
    assert (
        "已揭露的內扣費用（經理費 / 保管費）" in by_metric
    )  # never a bare dash when half is known
    assert (
        "近 5 年累計報酬" not in by_metric
    )  # the source never fills it; 5y price return covers it
    stability = by_metric["配息穩定性"]
    # Both pay every year; the side with fewer cuts wins the tie instead of a blanket "持平".
    assert stability.stronger in {"A", "B"} or stability.a == stability.b


def test_stability_breaks_a_tie_on_fewer_cuts() -> None:
    def etf_side(symbol_id: str, dividends: list[float]) -> _Side:
        rows = tuple(
            EtfAnnualRow(
                year=2020 + i, label=str(2020 + i), complete=True, cash_dividend_adjusted=amount
            )
            for i, amount in enumerate(dividends)
        )
        return _Side(
            symbol=Symbol(id=symbol_id, market=Market.TW),
            kind="etf",
            risk=_RiskBundle(metrics=RiskMetrics(), benchmark="x", indexed=()),
            etf_rows=rows,
        )

    steady = etf_side("A", [1.0, 1.1, 1.2, 1.3])  # 4/4 paid, 0 cuts
    choppy = etf_side("B", [1.0, 0.8, 1.0, 0.7])  # 4/4 paid, 2 cuts

    assert _stability_row(steady, choppy).stronger == "A"
    assert _stability_row(choppy, steady).stronger == "B"
    assert _stability_row(steady, steady).stronger == "tie"
