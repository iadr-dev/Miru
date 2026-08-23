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
