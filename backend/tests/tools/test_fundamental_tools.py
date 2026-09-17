from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import MaskedTeaser, ToolContext, dispatch_tool
from tools.common import DateRangeInput
from tools.fundamental_tools import (
    FinancialStatementType,
    GetCorporateActionEventsTool,
    GetDividendPolicySummaryTool,
    GetFinancialStatementInput,
    GetFinancialStatementTool,
    GetMarketValueTool,
    GetMonthRevenueTool,
)


def _result(
    payload: dict[str, object], *, data_class: DataClass = DataClass.PUBLIC
) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=data_class,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_financial_statement_selects_balance_sheet_capability() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    line = {
        "symbol": "2330",
        "market": "TW",
        "period_end": "2026-06-30",
        "statement_type": "balance_sheet",
        "item": "TotalAssets",
        "origin_name": "資產總額",
        "value": 100.0,
    }
    router, adapter = make_router(symbol, {Capability.BALANCE_SHEET: _result({"lines": [line]})})
    tool = GetFinancialStatementTool(router)

    output = await tool.run(
        GetFinancialStatementInput(
            id="2330", market=Market.TW, statement_type=FinancialStatementType.BALANCE_SHEET
        ),
        ToolContext(),
    )

    assert adapter.calls[0][0] is Capability.BALANCE_SHEET
    assert output.lines[0].value == 100.0


async def test_get_financial_statement_selects_income_statement_capability() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    line = {
        "symbol": "2330",
        "market": "TW",
        "period_end": "2026-06-30",
        "statement_type": "income_statement",
        "item": "Revenue",
        "origin_name": "營業收入",
        "value": 500.0,
    }
    router, adapter = make_router(symbol, {Capability.FINANCIALS: _result({"lines": [line]})})
    tool = GetFinancialStatementTool(router)

    output = await tool.run(
        GetFinancialStatementInput(
            id="2330", market=Market.TW, statement_type=FinancialStatementType.INCOME_STATEMENT
        ),
        ToolContext(),
    )

    assert adapter.calls[0][0] is Capability.FINANCIALS
    assert output.lines[0].value == 500.0


async def test_get_market_value_is_sponsor_gated() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    row = {"symbol": "2330", "market": "TW", "trade_date": "2026-08-01", "market_value": 1000}
    router, _ = make_router(
        symbol, {Capability.MARKET_VALUE: _result({"rows": [row]}, data_class=DataClass.SPONSOR)}
    )
    tool = GetMarketValueTool(router)
    assert tool.data_class is DataClass.SPONSOR
    args = DateRangeInput(id="2330", market=Market.TW)

    masked = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))
    assert isinstance(masked, MaskedTeaser)


async def test_get_month_revenue_computes_mom_and_yoy_trend() -> None:
    """Regression guard for the earnings-quality zero-prose bug (same class as Finding #69/#85):
    the model was observed hand-transcribing raw month-revenue rows into a markdown table and
    running out of response budget before writing any prose. `trend` must carry pre-computed
    MoM/YoY so the model can cite it directly."""
    symbol = Symbol(id="2317", market=Market.TW)
    rows = [
        {
            "symbol": "2317",
            "market": "TW",
            "trade_date": f"{year}-{month:02d}-10",
            "country": "TW",
            "revenue": revenue,
            "revenue_month": month,
            "revenue_year": year,
        }
        for year, month, revenue in [
            (2025, 6, 100_000),
            (2025, 7, 110_000),
            (2026, 7, 121_000),
        ]
    ]
    router, _adapter = make_router(symbol, {Capability.MONTH_REVENUE: _result({"rows": rows})})
    tool = GetMonthRevenueTool(router)

    output = await tool.run(DateRangeInput(id="2317", market=Market.TW), ToolContext())

    assert len(output.trend) == 3
    first, second, third = output.trend
    assert first.mom_pct is None
    assert first.yoy_pct is None
    assert second.mom_pct == 0.1
    assert second.yoy_pct is None
    assert third.mom_pct is None  # no 2026-06 row to diff against
    assert third.yoy_pct == 0.1


async def test_get_month_revenue_computes_cumulative_yoy_and_period_high() -> None:
    symbol = Symbol(id="2317", market=Market.TW)
    rows = [
        {
            "symbol": "2317",
            "market": "TW",
            "trade_date": f"{year}-{month + 1:02d}-10",
            "country": "TW",
            "revenue": revenue,
            "revenue_month": month,
            "revenue_year": year,
        }
        for year, month, revenue in [
            (2025, 1, 100_000),
            (2025, 2, 90_000),
            (2026, 1, 120_000),
            (2026, 2, 80_000),
        ]
    ]
    router, _adapter = make_router(symbol, {Capability.MONTH_REVENUE: _result({"rows": rows})})
    tool = GetMonthRevenueTool(router)

    output = await tool.run(DateRangeInput(id="2317", market=Market.TW), ToolContext())

    by_month = {(p.revenue_year, p.revenue_month): p for p in output.trend}
    # 2026/01: cumulative = 120,000 vs. 2025/01 cumulative 100,000 -> +20%.
    assert by_month[(2026, 1)].cumulative_yoy_pct == 0.2
    # 2026/02: cumulative (120k+80k)=200k vs. 2025 (100k+90k)=190k -> ~+5.26%.
    assert by_month[(2026, 2)].cumulative_yoy_pct is not None
    assert round(by_month[(2026, 2)].cumulative_yoy_pct, 4) == round(200_000 / 190_000 - 1, 4)
    # 2026/01 (120k) beats every prior January (100k) -> period high; 2025/01 was itself the
    # only January seen so far, so it's trivially also a high.
    assert by_month[(2026, 1)].is_period_high is True
    assert by_month[(2025, 1)].is_period_high is True
    # 2026/02 (80k) is below 2025/02 (90k) -> not a period high.
    assert by_month[(2026, 2)].is_period_high is False


async def test_get_month_revenue_period_end_close_from_price_eod() -> None:
    symbol = Symbol(id="2317", market=Market.TW)
    rows = [
        {
            "symbol": "2317",
            "market": "TW",
            "trade_date": "2026-02-10",
            "country": "TW",
            "revenue": 100_000,
            "revenue_month": 1,
            "revenue_year": 2026,
        }
    ]
    bars = [
        {
            "symbol": "2317",
            "market": "TW",
            "trade_date": "2026-01-30",
            "open": 50.0,
            "high": 51.0,
            "low": 49.0,
            "close": 50.5,
            "volume": 1000,
        },
        # After the month-end anchor (2026-02-09, one day before the 2026-02-10 filing) —
        # must NOT be picked as this month's period-end close.
        {
            "symbol": "2317",
            "market": "TW",
            "trade_date": "2026-02-15",
            "open": 60.0,
            "high": 61.0,
            "low": 59.0,
            "close": 60.5,
            "volume": 1000,
        },
    ]
    router, _adapter = make_router(
        symbol,
        {
            Capability.MONTH_REVENUE: _result({"rows": rows}),
            Capability.PRICE_EOD: _result({"bars": bars}),
        },
    )
    tool = GetMonthRevenueTool(router)

    output = await tool.run(DateRangeInput(id="2317", market=Market.TW), ToolContext())

    assert len(output.trend) == 1
    assert output.trend[0].period_end_close == 50.5


async def test_get_month_revenue_trend_empty_when_no_rows() -> None:
    symbol = Symbol(id="2317", market=Market.TW)
    router, _adapter = make_router(symbol, {Capability.MONTH_REVENUE: _result({"rows": []})})
    tool = GetMonthRevenueTool(router)

    output = await tool.run(DateRangeInput(id="2317", market=Market.TW), ToolContext())

    assert output.trend == ()
    assert output.rows == ()


async def test_get_corporate_action_events_bundles_four_datasets() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.CAPITAL_REDUCTION_REFERENCE_PRICE: _result({"rows": []}),
            Capability.STOCK_DELISTING: _result({"rows": []}),
            Capability.SPLIT_PRICE: _result({"rows": []}),
            Capability.PAR_VALUE_CHANGE: _result({"rows": []}),
        },
    )
    tool = GetCorporateActionEventsTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert output.capital_reduction == ()
    assert output.delisting == ()
    assert output.split_price == ()
    assert output.par_value_change == ()


async def test_get_dividend_policy_summary_computes_year_totals_and_stats() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    events = [
        {
            "symbol": "2330",
            "market": "TW",
            "announced_date": "2025-05-28",
            "fiscal_year": "113年第4季",
            "cash_dividend": 4.5,
            "stock_dividend": 0.0,
            "cash_ex_dividend_date": "2025-06-12",
        },
        {
            "symbol": "2330",
            "market": "TW",
            "announced_date": "2025-03-03",
            "fiscal_year": "113年第3季",
            "cash_dividend": 4.5,
            "stock_dividend": 0.0,
            "cash_ex_dividend_date": "2025-03-18",
        },
    ]
    dividend_result_rows = [
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": "2025-03-18",
            "before_price": 1000.0,
            "after_price": 995.5,
            "stock_and_cache_dividend": 4.5,
            "stock_or_cache_dividend": "息",
            "max_price": 1010.0,
            "min_price": 990.0,
            "open_price": 995.5,
            "reference_price": 995.5,
        },
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": "2025-06-12",
            "before_price": 1050.0,
            "after_price": 1045.5,
            "stock_and_cache_dividend": 4.5,
            "stock_or_cache_dividend": "息",
            "max_price": 1060.0,
            "min_price": 1040.0,
            "open_price": 1045.5,
            "reference_price": 1045.5,
        },
    ]
    eps_lines = [
        {
            "symbol": "2330",
            "market": "TW",
            "period_end": "2024-03-31",
            "statement_type": "income_statement",
            "item": "EPS",
            "origin_name": "基本每股盈餘",
            "value": 8.0,
        },
        {
            "symbol": "2330",
            "market": "TW",
            "period_end": "2024-06-30",
            "statement_type": "income_statement",
            "item": "EPS",
            "origin_name": "基本每股盈餘",
            "value": 9.0,
        },
    ]
    bars = [
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": "2025-03-18",
            "open": 995.5,
            "high": 1000.0,
            "low": 990.0,
            "close": 995.5,
            "volume": 1000,
        },
        # Recovers to the first event's before_price (1000.0) before the next ex-date.
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": "2025-04-01",
            "open": 1000.0,
            "high": 1005.0,
            "low": 999.0,
            "close": 1002.0,
            "volume": 1000,
        },
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": "2025-06-12",
            "open": 1045.5,
            "high": 1050.0,
            "low": 1040.0,
            "close": 1045.5,
            "volume": 1000,
        },
        # Latest close — never recovers to the second event's before_price (1050.0).
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": "2025-06-13",
            "open": 1045.0,
            "high": 1048.0,
            "low": 1044.0,
            "close": 1046.0,
            "volume": 1000,
        },
    ]
    router, _ = make_router(
        symbol,
        {
            Capability.DIVIDENDS: _result({"events": events}),
            Capability.DIVIDEND_RESULT: _result({"rows": dividend_result_rows}),
            Capability.FINANCIALS: _result({"lines": eps_lines}),
            Capability.PRICE_EOD: _result({"bars": bars}),
        },
    )
    tool = GetDividendPolicySummaryTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert len(output.year_summaries) == 1
    year_summary = output.year_summaries[0]
    assert year_summary.year == 2024  # 113 + 1911
    assert year_summary.cash_dividend == 9.0
    assert year_summary.total_dividend == 9.0

    assert len(output.fill_results) == 2
    first_fill, second_fill = output.fill_results
    assert first_fill.filled is True
    assert first_fill.filled_date is not None
    assert second_fill.filled is False  # latest close (1046.0) never reached 1050.0

    stats = output.stats
    assert stats.latest_year == 2024
    assert stats.latest_year_total_dividend == 9.0
    assert stats.cash_dividend_yield_pct is not None
    assert stats.payout_ratio_pct is not None
    assert stats.fill_rate_sample_size == 2
    assert stats.fill_rate_pct == 50.0


async def test_get_dividend_policy_summary_empty_when_no_events() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.DIVIDENDS: _result({"events": []}),
            Capability.DIVIDEND_RESULT: _result({"rows": []}),
            Capability.FINANCIALS: _result({"lines": []}),
            Capability.PRICE_EOD: _result({"bars": []}),
        },
    )
    tool = GetDividendPolicySummaryTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert output.year_summaries == ()
    assert output.fill_results == ()
    assert output.stats.latest_year is None
    assert output.stats.fill_rate_pct is None
