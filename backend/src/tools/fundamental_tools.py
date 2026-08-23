"""Fundamental-category tools — the FinMind Fundamental-category datasets not already wired in
`market_tools.py` (`dividends`). `financials`/`balance_sheet`/`cash_flow_statement` (income
statement, balance sheet, cash-flow statement) are folded into one `get_financial_statement`
tool alongside a `statement_type` selector rather than three near-duplicate tools, matching the
same shared row shape their adapters already share
(`schemas.capabilities.FinancialStatementLine`). Corporate-action reference rows (capital
reduction, delisting, split, par-value change) are bundled into `get_corporate_action_events`.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import (
    CapitalReductionReferencePriceRow,
    DividendResultRow,
    FinancialStatementLine,
    MarketValueRow,
    MarketValueWeightRow,
    MonthRevenueRow,
    ParValueChangeRow,
    SplitPriceRow,
    StockDelistingRow,
)
from schemas.enums import Capability, DataClass
from schemas.source import HealthStatus
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import DateRangeInput


class _RowsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus


class FinancialStatementType(StrEnum):
    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"


_STATEMENT_CAPABILITY: dict[FinancialStatementType, Capability] = {
    # `Capability.FINANCIALS` already wraps FinMind's `TaiwanStockFinancialStatements`
    # (綜合損益表) for TW and yfinance's income-statement equivalent elsewhere, sharing the
    # exact `item`/`origin_name`/`value`/`period_end` row shape `balance_sheet`/`cash_flow`
    # already use — no new capability needed, just this selector wiring.
    FinancialStatementType.INCOME_STATEMENT: Capability.FINANCIALS,
    FinancialStatementType.BALANCE_SHEET: Capability.BALANCE_SHEET,
    FinancialStatementType.CASH_FLOW: Capability.CASH_FLOW_STATEMENT,
}


class GetFinancialStatementInput(DateRangeInput):
    statement_type: FinancialStatementType = Field(
        description="Which statement to fetch — 'income_statement' (revenue/COGS/gross "
        "margin/EPS), 'balance_sheet', or 'cash_flow'."
    )


class GetFinancialStatementOutput(_RowsOutput):
    lines: tuple[FinancialStatementLine, ...] = Field(default_factory=tuple)


class GetFinancialStatementTool(BaseTool):
    """`get_financial_statement` — wraps `Capability.FINANCIALS`/`Capability.BALANCE_SHEET`/
    `Capability.CASH_FLOW_STATEMENT`, selected by `statement_type`."""

    name = "get_financial_statement"
    description = (
        "Get income-statement (revenue/COGS/gross margin/EPS), balance-sheet, or "
        "cash-flow-statement line items for one stock."
    )
    input_model: type[BaseModel] = GetFinancialStatementInput
    output_model: type[BaseModel] = GetFinancialStatementOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "financial_statement"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetFinancialStatementInput, args)
        result, lines = await fetch_rows(
            self._router,
            _STATEMENT_CAPABILITY[typed.statement_type],
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=FinancialStatementLine,
            key="lines",
            params=typed.date_params(),
        )
        return GetFinancialStatementOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            lines=lines,
        )


class GetDividendResultOutput(_RowsOutput):
    rows: tuple[DividendResultRow, ...] = Field(default_factory=tuple)


class GetDividendResultTool(BaseTool):
    """`get_dividend_result` — wraps `Capability.DIVIDEND_RESULT` (除權除息結果; distinct from
    `get_dividend_schedule`'s announced-policy timeline — this is the settled outcome)."""

    name = "get_dividend_result"
    description = "Get 除權除息結果 (ex-dividend/ex-rights settled-result) rows for one stock."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetDividendResultOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "dividend_result"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.DIVIDEND_RESULT,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=DividendResultRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetDividendResultOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class MonthRevenueTrendPoint(BaseModel):
    """One filed month's revenue plus its pre-computed MoM/YoY change — so a skill asking for a
    "近期月營收趨勢" never forces the model to manually diff adjacent rows itself (the same
    failure class as `InstitutionalFlowRollingNet`: a live `earnings-quality` turn's reasoning
    trace showed the model hand-transcribing raw month-revenue rows into a markdown table and
    running out of response budget before writing any prose — the final answer had zero
    analysis). `mom_pct`/`yoy_pct` are `None` when the comparison month is absent from `rows`,
    never a fabricated figure from a mismatched month."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    revenue_year: int
    revenue_month: int
    revenue: int
    mom_pct: float | None = None
    yoy_pct: float | None = None


def _safe_pct_change(current: int, prior: int | None) -> float | None:
    if prior is None or prior == 0:
        return None
    return (current - prior) / prior


def _prior_calendar_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _month_revenue_trend(rows: Sequence[MonthRevenueRow]) -> tuple[MonthRevenueTrendPoint, ...]:
    ordered = sorted(rows, key=lambda row: (row.revenue_year, row.revenue_month))
    by_year_month = {(row.revenue_year, row.revenue_month): row.revenue for row in ordered}
    points: list[MonthRevenueTrendPoint] = []
    for row in ordered:
        prior_month_revenue = by_year_month.get(
            _prior_calendar_month(row.revenue_year, row.revenue_month)
        )
        prior_year_revenue = by_year_month.get((row.revenue_year - 1, row.revenue_month))
        points.append(
            MonthRevenueTrendPoint(
                revenue_year=row.revenue_year,
                revenue_month=row.revenue_month,
                revenue=row.revenue,
                mom_pct=_safe_pct_change(row.revenue, prior_month_revenue),
                yoy_pct=_safe_pct_change(row.revenue, prior_year_revenue),
            )
        )
    return tuple(points)


class GetMonthRevenueOutput(_RowsOutput):
    # `trend` is declared BEFORE `rows` — same field-order-survives-truncation reasoning as
    # `GetInstitutionalFlowOutput.rolling_net` — and, independent of truncation, gives the model
    # a pre-computed MoM/YoY figure per month so it never hand-diffs adjacent rows itself.
    trend: tuple[MonthRevenueTrendPoint, ...] = Field(default_factory=tuple)
    rows: tuple[MonthRevenueRow, ...] = Field(default_factory=tuple)


class GetMonthRevenueTool(BaseTool):
    """`get_month_revenue` — wraps `Capability.MONTH_REVENUE` (月營收 filing). Also returns
    `trend` — server-side pre-computed MoM/YoY change per filed month (see
    `MonthRevenueTrendPoint`'s own docstring for why)."""

    name = "get_month_revenue"
    description = (
        "Get monthly revenue (月營收) filing rows for one stock, plus a pre-computed MoM/YoY "
        "change per month — cite `trend`'s figures directly rather than diffing `rows` yourself."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetMonthRevenueOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "month_revenue"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.MONTH_REVENUE,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=MonthRevenueRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetMonthRevenueOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
            trend=_month_revenue_trend(rows),
        )


class GetMarketValueOutput(_RowsOutput):
    rows: tuple[MarketValueRow, ...] = Field(default_factory=tuple)


class GetMarketValueTool(BaseTool):
    """`get_market_value` — wraps `Capability.MARKET_VALUE` (市值; Backer/Sponsor-only, no free
    per-symbol tier upstream)."""

    name = "get_market_value"
    description = "Get daily market-capitalization rows for one stock (Sponsor-tier)."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetMarketValueOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "market_value"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.MARKET_VALUE,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=MarketValueRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetMarketValueOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetMarketValueWeightOutput(_RowsOutput):
    rows: tuple[MarketValueWeightRow, ...] = Field(default_factory=tuple)


class GetMarketValueWeightTool(BaseTool):
    """`get_market_value_weight` — wraps `Capability.MARKET_VALUE_WEIGHT` (市值比重排行)."""

    name = "get_market_value_weight"
    description = (
        "Get the daily whole-market market-cap-weight ranking rows for one stock (Sponsor-tier)."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetMarketValueWeightOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "market_value_weight"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result, rows = await fetch_rows(
            self._router,
            Capability.MARKET_VALUE_WEIGHT,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            model=MarketValueWeightRow,
            key="rows",
            params=typed.date_params(),
        )
        return GetMarketValueWeightOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
        )


class GetCorporateActionEventsOutput(BaseModel):
    """Bundles the four small corporate-action reference-price/event datasets into one report
    rather than four near-duplicate single-field tools, per this registry's bundling
    convention. Any of the four may be empty independently — each has its own honest
    `source_id`/`caveat` tag inline on its row set's parent tool if a caller needs provenance
    per-field; this composite intentionally keeps one shared top-level caveat, since all four
    calls share the same symbol/date-range request and event-driven nature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    currentness_caveat: str = Field(min_length=1)
    capital_reduction: tuple[CapitalReductionReferencePriceRow, ...] = Field(default_factory=tuple)
    delisting: tuple[StockDelistingRow, ...] = Field(default_factory=tuple)
    split_price: tuple[SplitPriceRow, ...] = Field(default_factory=tuple)
    par_value_change: tuple[ParValueChangeRow, ...] = Field(default_factory=tuple)


class GetCorporateActionEventsTool(BaseTool):
    """`get_corporate_action_events` — bundles `Capability.CAPITAL_REDUCTION_REFERENCE_PRICE`
    (減資恢復買賣參考價), `Capability.STOCK_DELISTING` (下市櫃), `Capability.SPLIT_PRICE` (分割/
    反分割), and `Capability.PAR_VALUE_CHANGE` (變更面額) into one report for one stock. All
    four are event-driven and individually sparse — bundling avoids four near-empty tools the
    agent would otherwise have to call separately to build one "what corporate actions has this
    stock had" picture."""

    name = "get_corporate_action_events"
    description = (
        "Get capital-reduction, delisting, split/reverse-split, and par-value-change "
        "reference-price/event rows for one stock in one bundled report."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetCorporateActionEventsOutput
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        symbol = typed.to_symbol()
        params = typed.date_params()

        cap_reduction_result, cap_reduction_rows = await fetch_rows(
            self._router,
            Capability.CAPITAL_REDUCTION_REFERENCE_PRICE,
            symbol,
            tls_policy=ctx.tls_policy,
            model=CapitalReductionReferencePriceRow,
            key="rows",
            params=params,
        )
        _delisting_result, delisting_rows = await fetch_rows(
            self._router,
            Capability.STOCK_DELISTING,
            symbol,
            tls_policy=ctx.tls_policy,
            model=StockDelistingRow,
            key="rows",
            params=params,
        )
        _split_result, split_rows = await fetch_rows(
            self._router,
            Capability.SPLIT_PRICE,
            symbol,
            tls_policy=ctx.tls_policy,
            model=SplitPriceRow,
            key="rows",
            params=params,
        )
        _par_value_result, par_value_rows = await fetch_rows(
            self._router,
            Capability.PAR_VALUE_CHANGE,
            symbol,
            tls_policy=ctx.tls_policy,
            model=ParValueChangeRow,
            key="rows",
            params=params,
        )
        return GetCorporateActionEventsOutput(
            currentness_caveat=cap_reduction_result.currentness_caveat,
            capital_reduction=cap_reduction_rows,
            delisting=delisting_rows,
            split_price=split_rows,
            par_value_change=par_value_rows,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [
        (GetFinancialStatementTool(router), False),
        (GetDividendResultTool(router), False),
        (GetMonthRevenueTool(router), False),
        (GetMarketValueTool(router), False),
        (GetMarketValueWeightTool(router), False),
        (GetCorporateActionEventsTool(router), False),
    ]


__all__ = [
    "FinancialStatementType",
    "GetCorporateActionEventsOutput",
    "GetCorporateActionEventsTool",
    "GetDividendResultOutput",
    "GetDividendResultTool",
    "GetFinancialStatementInput",
    "GetFinancialStatementOutput",
    "GetFinancialStatementTool",
    "GetMarketValueOutput",
    "GetMarketValueTool",
    "GetMarketValueWeightOutput",
    "GetMarketValueWeightTool",
    "GetMonthRevenueOutput",
    "GetMonthRevenueTool",
    "MonthRevenueTrendPoint",
    "build_registration_table",
]
