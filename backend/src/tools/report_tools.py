"""Tools behind the analysis reports: `get_annual_fundamentals` (multi-year table + chart data),
`get_revenue_seasonality`, `get_risk_metrics` (Beta / drawdown / return) and
`get_comparison_metrics` (the side-by-side table for two symbols).

Why tools and not prompt arithmetic: a five-year table is sums over hundreds of quarterly statement
lines, an average-equity ROE, year-end ratios and a growth-adjusted seasonal index; Beta and
drawdown are statistics over ~1,250 daily closes. Handing raw lines to the model means
hand-transcribing them (a live run exhausted its budget doing exactly that, see
`MonthRevenueTrendPoint`) and trusting its arithmetic. Here the numbers are computed once, in
`analytics`, and the model writes the analysis around them.

All of them read the same per-symbol capabilities the raw tools do — no whole-market read, no new
source. Charts are declarative `ChartHint`s over the output's own series (`chart_hints`, plural: one
card, several charts).
"""

from __future__ import annotations

import asyncio
import re
from datetime import date as date_
from itertools import pairwise
from statistics import fmean
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from analytics.annual_report import (
    attach_dividends,
    build_etf_annual_rows,
    build_tw_annual_rows,
    build_us_annual_rows,
    monthly_seasonality,
    quarterly_seasonality,
)
from analytics.risk_metrics import (
    adjust_discontinuities,
    annualised_volatility_pct,
    beta,
    indexed_monthly,
    max_drawdown_pct,
    price_cagr_pct,
    price_return_pct,
)
from config.settings import get_settings
from dataaccess.router import DataAccessRouter
from ingest.store import ParquetStore
from schemas.annual_report import (
    AnnualFundamentalsRow,
    EtfAnnualRow,
    SeasonalityPeriod,
    SeasonalitySummary,
    SeriesPoint,
)
from schemas.capabilities import (
    DividendEvent,
    EODBar,
    EtfExpenseInfo,
    EtfFundInfo,
    EtfTrailingReturns,
    FinancialStatementLine,
    FundHoldingRow,
    FundProfileRow,
    MonthRevenueRow,
)
from schemas.chart_hint import ChartHint, FieldDef, FieldType, Mark
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from schemas.source import HealthStatus
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import SymbolInput
from tools.etf_pcf_tools import (
    GetEtfPremiumDiscountInput,
    GetEtfPremiumDiscountOutput,
    GetEtfPremiumDiscountTool,
)

_DEFAULT_YEARS = 5
_MAX_YEARS = 7
_MIN_POINTS = 2  # a return / CAGR needs a start and an end

# Display units: TW amounts are in NT dollars, US in USD. Charts and the table use a readable unit.
_TW_UNIT = (1e8, "億元（新台幣）")
_US_UNIT = (1e9, "十億美元")
_YEAR = FieldDef(field="year", type=FieldType.ORDINAL)
_VALUE = FieldDef(field="value", type=FieldType.QUANTITATIVE)
_METRIC = FieldDef(field="metric", type=FieldType.NOMINAL)

# Beta is measured against a liquid, index-tracking proxy for each market. The proxy is named in
# every output so a reader never mistakes it for the index itself.
_BENCHMARK = {
    Market.TW: ("0050", "0050（元大台灣50，台股大盤代理）"),
    Market.US: ("SPY", "SPY（S&P 500 代理）"),
}
# A day-over-day move larger than this is treated as a split-like break (see
# `analytics.risk_metrics.adjust_discontinuities`). TW limits a day to ~10%; US has no limit, so
# only splits of 2:1 or more are caught.
_MAX_DAILY_MOVE = {Market.TW: 0.105, Market.US: 0.45}
_HIGHER = "越高越佳"
_LOWER = "越低越佳"


class _YearsInput(SymbolInput):
    years: int = Field(
        default=_DEFAULT_YEARS,
        ge=1,
        le=_MAX_YEARS,
        description="How many fiscal years to cover (default 5).",
    )


def _start_date(years: int) -> date_:
    """Jan 1 of the earliest year needed — one extra year back so the oldest row still has a prior
    year to compute YoY and average-equity ROE against."""
    return date_(date_.today().year - years, 1, 1)


def _scaled(value: float | None, divisor: float) -> float | None:
    return round(value / divisor, 2) if value is not None else None


def _series(
    rows: tuple[AnnualFundamentalsRow, ...], metric: str, pick: str, divisor: float = 1.0
) -> tuple[SeriesPoint, ...]:
    points: list[SeriesPoint] = []
    for row in rows:
        value = getattr(row, pick)
        if isinstance(value, int | float):
            points.append(
                SeriesPoint(year=row.label, metric=metric, value=round(float(value) / divisor, 2))
            )
    return tuple(points)


# ---- annual fundamentals ---------------------------------------------------------------------


class _AnnualBundle(BaseModel):
    """Everything one symbol's annual table needs, computed once and shared by the single-stock
    report tool and the comparison tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str
    health_status: HealthStatus
    unit: str
    divisor: float
    raw_rows: tuple[AnnualFundamentalsRow, ...]
    rows: tuple[AnnualFundamentalsRow, ...]


async def _load_annual(
    router: DataAccessRouter, ctx: ToolContext, symbol: Symbol, years: int
) -> _AnnualBundle:
    params: dict[str, object] = {"start_date": _start_date(years).isoformat()}
    is_tw = symbol.market is Market.TW

    async def statement(capability: Capability):  # noqa: ANN202 — local helper, used 3 times below
        return await fetch_rows(
            router,
            capability,
            symbol,
            tls_policy=ctx.tls_policy,
            model=FinancialStatementLine,
            key="lines",
            params=params,
        )

    (income_result, income), (_, balance), (_, cash) = await asyncio.gather(
        statement(Capability.FINANCIALS),
        statement(Capability.BALANCE_SHEET),
        statement(Capability.CASH_FLOW_STATEMENT),
    )
    (_, events), (_, bars) = await asyncio.gather(
        fetch_rows(
            router,
            Capability.DIVIDENDS,
            symbol,
            tls_policy=ctx.tls_policy,
            model=DividendEvent,
            key="events",
            params=params,
        ),
        fetch_rows(
            router,
            Capability.PRICE_EOD,
            symbol,
            tls_policy=ctx.tls_policy,
            model=EODBar,
            key="bars",
            params=params,
        ),
    )
    build = build_tw_annual_rows if is_tw else build_us_annual_rows
    raw = tuple(
        attach_dividends(
            build(list(income), list(balance), list(cash), years=years),
            list(events),
            list(bars),
            roc_fiscal_year=is_tw,
        )
    )
    divisor, unit = _TW_UNIT if is_tw else _US_UNIT
    scaled = tuple(
        row.model_copy(
            update={
                "revenue": _scaled(row.revenue, divisor),
                "net_income": _scaled(row.net_income, divisor),
                "free_cash_flow": _scaled(row.free_cash_flow, divisor),
            }
        )
        for row in raw
    )
    return _AnnualBundle(
        source_id=income_result.source_id,
        currentness_caveat=income_result.currentness_caveat,
        health_status=income_result.health.status,
        unit=unit,
        divisor=divisor,
        raw_rows=raw,
        rows=scaled,
    )


class GetAnnualFundamentalsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    market: Market
    amount_unit: str
    rows: tuple[AnnualFundamentalsRow, ...] = Field(default_factory=tuple)
    coverage_note: str | None = None
    # Chart series (long format: year / metric / value) — one array per chart, referenced by the
    # matching `chart_hints` entry's `data_key`.
    revenue_series: tuple[SeriesPoint, ...] = Field(default_factory=tuple)
    growth_series: tuple[SeriesPoint, ...] = Field(default_factory=tuple)
    eps_series: tuple[SeriesPoint, ...] = Field(default_factory=tuple)
    margin_series: tuple[SeriesPoint, ...] = Field(default_factory=tuple)
    dividend_series: tuple[SeriesPoint, ...] = Field(default_factory=tuple)
    chart_hints: tuple[ChartHint, ...] = Field(default_factory=tuple)


def _hints(unit: str, out: dict[str, tuple[SeriesPoint, ...]]) -> tuple[ChartHint, ...]:
    """One hint per non-empty series — an empty series would render as an empty chart."""
    candidates = (
        ("revenue_series", Mark.BAR, f"營收（{unit}）", False),
        ("growth_series", Mark.LINE, "營收年增率（%）", False),
        ("eps_series", Mark.BAR, "每股盈餘 EPS（元）", False),
        ("margin_series", Mark.LINE, "獲利能力：毛利率、營業利益率、ROE（%）", True),
        ("dividend_series", Mark.BAR, "每股股利（元）", True),
    )
    return tuple(
        ChartHint(
            mark=mark,
            title=title,
            data_key=key,
            x=_YEAR,
            y=_VALUE,
            color=_METRIC if colored else None,
        )
        for key, mark, title, colored in candidates
        if out.get(key)
    )


class GetAnnualFundamentalsTool(BaseTool):
    """`get_annual_fundamentals` — the multi-year fundamentals table for one stock."""

    name = "get_annual_fundamentals"
    description = (
        "Get a multi-year (default 5) fundamentals table for ONE stock, already computed: per "
        "fiscal year revenue, revenue YoY, gross/operating margin, net income, EPS, ROE, debt "
        "ratio, current ratio, free cash flow, and (when available) cash/stock dividend per share "
        "and dividend yield. Taiwan and US stocks. Cite the rows directly; never re-derive "
        "ratios. Amounts are in `amount_unit`. A year still in progress is marked incomplete "
        "(YTD figures, no ROE, no yield). Also returns the chart series for this table; the "
        "charts render automatically."
    )
    input_model: type[BaseModel] = _YearsInput
    output_model: type[BaseModel] = GetAnnualFundamentalsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "annual_fundamentals"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(_YearsInput, args)
        bundle = await _load_annual(self._router, ctx, typed.to_symbol(), typed.years)
        series = {
            "revenue_series": _series(bundle.raw_rows, "營收", "revenue", bundle.divisor),
            "growth_series": _series(bundle.raw_rows, "營收年增率", "revenue_yoy_pct"),
            "eps_series": _series(bundle.raw_rows, "EPS", "eps"),
            "margin_series": (
                _series(bundle.raw_rows, "毛利率", "gross_margin_pct")
                + _series(bundle.raw_rows, "營業利益率", "operating_margin_pct")
                + _series(bundle.raw_rows, "ROE", "roe_pct")
            ),
            "dividend_series": (
                _series(bundle.raw_rows, "現金股利", "cash_dividend")
                + _series(bundle.raw_rows, "股票股利", "stock_dividend")
            ),
        }
        note = None
        if len(bundle.rows) < typed.years:
            note = f"資料來源目前只提供 {len(bundle.rows)} 個年度（要求 {typed.years} 年）"
        return GetAnnualFundamentalsOutput(
            source_id=bundle.source_id,
            currentness_caveat=bundle.currentness_caveat,
            health_status=bundle.health_status,
            market=typed.market,
            amount_unit=bundle.unit,
            rows=bundle.rows,
            coverage_note=note,
            chart_hints=_hints(bundle.unit, series),
            **series,
        )


# ---- seasonality -----------------------------------------------------------------------------


class _SeasonBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str
    health_status: HealthStatus
    periods: tuple[SeasonalityPeriod, ...]
    summary: SeasonalitySummary | None
    reason: str | None


async def _load_seasonality(
    router: DataAccessRouter, ctx: ToolContext, symbol: Symbol, years: int
) -> _SeasonBundle:
    params: dict[str, object] = {"start_date": _start_date(years).isoformat()}
    if symbol.market is Market.TW:
        result, month_rows = await fetch_rows(
            router,
            Capability.MONTH_REVENUE,
            symbol,
            tls_policy=ctx.tls_policy,
            model=MonthRevenueRow,
            key="rows",
            params=params,
        )
        periods, summary, reason = monthly_seasonality(list(month_rows), years=years)
    else:
        result, statement_lines = await fetch_rows(
            router,
            Capability.FINANCIALS,
            symbol,
            tls_policy=ctx.tls_policy,
            model=FinancialStatementLine,
            key="lines",
            params=params,
        )
        periods, summary, reason = quarterly_seasonality(list(statement_lines), years=years)
    return _SeasonBundle(
        source_id=result.source_id,
        currentness_caveat=result.currentness_caveat,
        health_status=result.health.status,
        periods=tuple(periods),
        summary=summary,
        reason=reason,
    )


class GetRevenueSeasonalityOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    market: Market
    periods: tuple[SeasonalityPeriod, ...] = Field(default_factory=tuple)
    summary: SeasonalitySummary | None = None
    unavailable_reason: str | None = None
    chart_hint: ChartHint | None = None


class GetRevenueSeasonalityTool(BaseTool):
    """`get_revenue_seasonality` — which months/quarters are seasonally strong or weak."""

    name = "get_revenue_seasonality"
    description = (
        "Get the revenue seasonality of ONE stock from its last ~5 complete years: Taiwan uses "
        "monthly revenue (peak/trough months, peak/trough quarter, 淡旺季差異%); US uses "
        "quarterly revenue when the source has at least two complete years, else says it is "
        "unavailable. Growth between years is removed, so this reflects seasonality, not the "
        "growth trend. Cite `summary` and `periods` directly; the chart renders automatically."
    )
    input_model: type[BaseModel] = _YearsInput
    output_model: type[BaseModel] = GetRevenueSeasonalityOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "revenue_seasonality"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(_YearsInput, args)
        bundle = await _load_seasonality(self._router, ctx, typed.to_symbol(), typed.years)
        return GetRevenueSeasonalityOutput(
            source_id=bundle.source_id,
            currentness_caveat=bundle.currentness_caveat,
            health_status=bundle.health_status,
            market=typed.market,
            periods=bundle.periods,
            summary=bundle.summary,
            unavailable_reason=bundle.reason,
            chart_hint=ChartHint(
                mark=Mark.BAR,
                title="營收季節指數偏離（%，0 = 全年平均）",
                data_key="periods",
                x=FieldDef(field="label", type=FieldType.ORDINAL),
                y=FieldDef(field="deviation_pct", type=FieldType.QUANTITATIVE),
            )
            if bundle.periods
            else None,
        )


# ---- risk ------------------------------------------------------------------------------------


class RiskMetrics(BaseModel):
    """Price-based figures (closing prices, dividends not reinvested) over the available history."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    years_covered: float | None = None
    price_return_pct: float | None = None
    price_cagr_pct: float | None = None
    annualised_volatility_pct: float | None = None
    max_drawdown_pct: float | None = None
    beta: float | None = None


class _RiskBundle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metrics: RiskMetrics
    benchmark: str
    indexed: tuple[tuple[str, float], ...]
    reason: str | None = None
    # Dates of split-like price breaks that were rebased before computing (empty = none found).
    adjusted_breaks: tuple[date_, ...] = ()


async def _closes(
    router: DataAccessRouter, ctx: ToolContext, symbol: Symbol, years: int
) -> tuple[list[tuple[date_, float]], list[date_]]:
    """Closes rebased across split-like breaks, plus the break dates (a report discloses them)."""
    today = date_.today()
    _, bars = await fetch_rows(
        router,
        Capability.PRICE_EOD,
        symbol,
        tls_policy=ctx.tls_policy,
        model=EODBar,
        key="bars",
        params={"start_date": date_(today.year - years, today.month, 1).isoformat()},
    )
    return adjust_discontinuities(
        [(bar.trade_date, bar.close) for bar in bars], max_abs_move=_MAX_DAILY_MOVE[symbol.market]
    )


async def _load_risk(
    router: DataAccessRouter, ctx: ToolContext, symbol: Symbol, years: int
) -> _RiskBundle:
    bench_id, bench_label = _BENCHMARK.get(symbol.market, _BENCHMARK[Market.TW])
    (asset, asset_breaks), (bench, _) = await asyncio.gather(
        _closes(router, ctx, symbol, years),
        _closes(router, ctx, Symbol(id=bench_id, market=symbol.market), years),
    )
    if len(asset) < _MIN_POINTS:
        return _RiskBundle(
            metrics=RiskMetrics(), benchmark=bench_label, indexed=(), reason="沒有可用的歷史價格"
        )
    span_days = (max(d for d, _ in asset) - min(d for d, _ in asset)).days
    return _RiskBundle(
        metrics=RiskMetrics(
            years_covered=round(span_days / 365.25, 1),
            price_return_pct=price_return_pct(asset),
            price_cagr_pct=price_cagr_pct(asset),
            annualised_volatility_pct=annualised_volatility_pct(asset),
            max_drawdown_pct=max_drawdown_pct(asset),
            beta=beta(asset, bench) if bench else None,
        ),
        benchmark=bench_label,
        indexed=tuple(indexed_monthly(asset)),
        adjusted_breaks=tuple(asset_breaks),
    )


class GetRiskMetricsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    symbol: str
    benchmark: str
    basis: str = "收盤價報酬（未含股利再投入）；Beta 為與大盤代理的日報酬相關性"
    metrics: RiskMetrics
    unavailable_reason: str | None = None
    # Split-like price breaks rebased before computing (e.g. a 4-for-1 split); empty = none found.
    adjusted_breaks: tuple[date_, ...] = ()
    price_series: tuple[SeriesPoint, ...] = Field(default_factory=tuple)
    chart_hint: ChartHint | None = None


class GetRiskMetricsTool(BaseTool):
    """`get_risk_metrics` — Beta, volatility, max drawdown and price return for one symbol."""

    name = "get_risk_metrics"
    description = (
        "Get price-based risk and return figures for ONE stock or ETF over the last ~5 years: "
        "Beta (vs a market proxy named in `benchmark`), annualised volatility, maximum drawdown, "
        "price return and CAGR (closing prices, dividends not reinvested). Also returns a "
        "rebased price line; the chart renders automatically. Cite the numbers directly."
    )
    input_model: type[BaseModel] = _YearsInput
    output_model: type[BaseModel] = GetRiskMetricsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "risk_metrics"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(_YearsInput, args)
        symbol = typed.to_symbol()
        bundle = await _load_risk(self._router, ctx, symbol, typed.years)
        points = tuple(
            SeriesPoint(year=month, metric=symbol.id, value=value)
            for month, value in bundle.indexed
        )
        return GetRiskMetricsOutput(
            market=typed.market,
            symbol=symbol.id,
            benchmark=bundle.benchmark,
            metrics=bundle.metrics,
            unavailable_reason=bundle.reason,
            adjusted_breaks=bundle.adjusted_breaks,
            price_series=points,
            chart_hint=ChartHint(
                mark=Mark.LINE,
                title=f"價格走勢（起點 = 100）：{symbol.id}",
                data_key="price_series",
                x=_YEAR,
                y=_VALUE,
            )
            if points
            else None,
        )


# ---- ETF per-year summary --------------------------------------------------------------------


class GetEtfAnnualSummaryOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    symbol: str
    basis: str = (
        "還原分割後的收盤價報酬；含息概估 = 價格報酬加計當年現金股利（不再投入，以年初價為分母）；"
        "殖利率 = 當年現金股利 ÷ 年底收盤價"
    )
    rows: tuple[EtfAnnualRow, ...] = Field(default_factory=tuple)
    adjusted_breaks: tuple[date_, ...] = ()
    return_series: tuple[SeriesPoint, ...] = Field(default_factory=tuple)
    yield_series: tuple[SeriesPoint, ...] = Field(default_factory=tuple)
    chart_hints: tuple[ChartHint, ...] = Field(default_factory=tuple)
    unavailable_reason: str | None = None


async def _load_etf_annual(
    router: DataAccessRouter, ctx: ToolContext, symbol: Symbol, years: int
) -> tuple[tuple[EtfAnnualRow, ...], list[date_]]:
    """Per-year ETF rows on split-adjusted prices, and the split-like breaks that were rebased."""
    start = date_(date_.today().year - years, 1, 1).isoformat()
    (_, bars), (_, events) = await asyncio.gather(
        fetch_rows(
            router,
            Capability.PRICE_EOD,
            symbol,
            tls_policy=ctx.tls_policy,
            model=EODBar,
            key="bars",
            params={"start_date": start},
        ),
        fetch_rows(
            router,
            Capability.DIVIDENDS,
            symbol,
            tls_policy=ctx.tls_policy,
            model=DividendEvent,
            key="events",
            params={"start_date": start},
        ),
    )
    raw = [(bar.trade_date, bar.close) for bar in bars]
    adjusted, breaks = adjust_discontinuities(raw, max_abs_move=_MAX_DAILY_MOVE[symbol.market])
    rows = tuple(
        build_etf_annual_rows(raw, adjusted, list(events), years=years, today=date_.today())
    )
    return rows, breaks


class GetEtfAnnualSummaryTool(BaseTool):
    """`get_etf_annual_summary` — per-calendar-year return, dividends and yield for one ETF."""

    name = "get_etf_annual_summary"
    description = (
        "Get a per-year table for ONE ETF (Taiwan or US) over ~5 years, already computed: year-end "
        "close, price return, cash dividend per share, dividend yield and a simple total return "
        "(price return plus dividends, not reinvested), on split-adjusted prices. Cite the rows "
        "directly. Also lists any split-like price breaks that were rebased. Charts render "
        "automatically."
    )
    input_model: type[BaseModel] = _YearsInput
    output_model: type[BaseModel] = GetEtfAnnualSummaryOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "etf_annual_summary"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(_YearsInput, args)
        symbol = typed.to_symbol()
        rows, breaks = await _load_etf_annual(self._router, ctx, symbol, typed.years)
        returns = tuple(
            SeriesPoint(year=row.label, metric=name, value=float(value))
            for row in rows
            for name, value in (
                ("價格報酬", row.price_return_pct),
                ("含息概估", row.total_return_simple_pct),
            )
            if value is not None
        )
        yields = tuple(
            SeriesPoint(year=row.label, metric="殖利率", value=row.dividend_yield_pct)
            for row in rows
            if row.dividend_yield_pct is not None
        )
        hints = tuple(
            ChartHint(
                mark=Mark.BAR,
                title=title,
                data_key=key,
                x=_YEAR,
                y=_VALUE,
                color=_METRIC if colored else None,
            )
            for key, title, colored, series in (
                ("return_series", "年度報酬（%）", True, returns),
                ("yield_series", "年度現金殖利率（%）", False, yields),
            )
            if series
        )
        return GetEtfAnnualSummaryOutput(
            market=typed.market,
            symbol=symbol.id,
            rows=rows,
            adjusted_breaks=tuple(breaks),
            return_series=returns,
            yield_series=yields,
            chart_hints=hints,
            unavailable_reason=None if rows else "沒有可用的歷史價格",
        )


# ---- comparison ------------------------------------------------------------------------------


class ComparisonInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id_a: str = Field(min_length=1, description="First stock/ETF code, e.g. '2330'.")
    market_a: Market
    id_b: str = Field(min_length=1, description="Second stock/ETF code, e.g. '2317'.")
    market_b: Market
    years: int = Field(default=_DEFAULT_YEARS, ge=2, le=_MAX_YEARS)


class ComparisonRow(BaseModel):
    """One line of the side-by-side table. `stronger` is decided by code from `direction` — the
    model does not judge it — and only states which side's number is better on THIS metric."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str
    a: str
    b: str
    stronger: Literal["A", "B", "tie"] | None
    direction: str


class GetComparisonMetricsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol_a: str
    symbol_b: str
    kind_a: Literal["stock", "etf"] = "stock"
    kind_b: Literal["stock", "etf"] = "stock"
    amount_unit_a: str
    amount_unit_b: str
    rows: tuple[ComparisonRow, ...] = Field(default_factory=tuple)
    notes: tuple[str, ...] = Field(default_factory=tuple)
    price_series: tuple[SeriesPoint, ...] = Field(default_factory=tuple)
    chart_hint: ChartHint | None = None


def _cagr(first: float | None, last: float | None, periods: int) -> float | None:
    if first is None or last is None or first <= 0 or last <= 0 or periods < 1:
        return None
    return round(((last / first) ** (1 / periods) - 1) * 100, 2)


def _fmt(value: float | None, suffix: str = "", digits: int = 2) -> str:
    return f"{value:.{digits}f}{suffix}" if value is not None else "—"


def _pick(
    a: float | None, b: float | None, *, higher_is_better: bool
) -> Literal["A", "B", "tie"] | None:
    if a is None or b is None:
        return None
    if a == b:
        return "tie"
    return "A" if (a > b) == higher_is_better else "B"


_TW_ETF_CODE = re.compile(r"^00\d{2,3}[A-Z]?$")
_FRACTION_LIMIT = 1.5  # weights that sum to <= this are fractions of one, not percents
_TOP_HOLDINGS = 10


class _EtfExtras(BaseModel):
    """ETF-only facts a stock does not have, each `None` when the source does not carry it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tracked: str | None = None
    aum: float | None = None
    expense_pct: float | None = None
    management_fee_pct: float | None = None
    custody_fee_pct: float | None = None
    premium_pct: float | None = None
    return_1y: float | None = None
    return_3y: float | None = None
    return_5y: float | None = None
    top10_weight_pct: float | None = None


async def _load_etf_extras(
    router: DataAccessRouter,
    store: ParquetStore | None,
    ctx: ToolContext,
    symbol: Symbol,
    profile: FundProfileRow | None,
) -> _EtfExtras:
    is_tw = symbol.market is Market.TW

    async def fetch(cap: Capability):  # noqa: ANN202 — local helper, parsed by its callers
        return await router.fetch(cap, symbol, tls_policy=ctx.tls_policy)

    trailing_result = await fetch(Capability.ETF_TRAILING_RETURNS)
    raw_returns = trailing_result.payload.get("returns")
    returns = (
        EtfTrailingReturns.model_validate(raw_returns) if isinstance(raw_returns, dict) else None
    )
    extras: dict[str, object] = {
        "return_1y": returns.return_1y_pct if returns else None,
        "return_3y": returns.return_3y_pct if returns else None,
        "return_5y": returns.return_5y_pct if returns else None,
    }
    if is_tw:
        info_result = await fetch(Capability.ETF_HOLDINGS)
        expense_result = await fetch(Capability.ETF_EXPENSE_RATIO)
        raw_info = info_result.payload.get("info")
        raw_expense = expense_result.payload.get("info")
        if isinstance(raw_info, dict):
            extras["tracked"] = EtfFundInfo.model_validate(raw_info).tracked_index
        if isinstance(raw_expense, dict):
            expense = EtfExpenseInfo.model_validate(raw_expense)
            extras["expense_pct"] = expense.total_expense_ratio_pct_approx
            extras["management_fee_pct"] = expense.management_fee_pct
            extras["custody_fee_pct"] = expense.custody_fee_pct
        if store is not None:
            premium = await GetEtfPremiumDiscountTool(store).run(
                GetEtfPremiumDiscountInput(id=symbol.id, market=symbol.market, days=5), ctx
            )
            if isinstance(premium, GetEtfPremiumDiscountOutput):
                extras["aum"] = premium.current_aum
                if premium.rows:
                    extras["premium_pct"] = premium.rows[-1].premium_discount_pct
    else:
        holdings_result = await fetch(Capability.FUND_HOLDINGS)
        raw_holdings = holdings_result.payload.get("holdings")
        if isinstance(raw_holdings, list) and raw_holdings:
            weights = [
                FundHoldingRow.model_validate(row).weight
                for row in cast("list[object]", raw_holdings)[:_TOP_HOLDINGS]
            ]
            total = sum(weights)
            extras["top10_weight_pct"] = round(
                total * 100 if total <= _FRACTION_LIMIT else total, 1
            )
        if profile is not None:
            extras["aum"] = profile.total_assets
            extras["tracked"] = profile.category
    return _EtfExtras.model_validate(extras)


async def _fund_profile(
    router: DataAccessRouter, ctx: ToolContext, symbol: Symbol
) -> FundProfileRow | None:
    result = await router.fetch(Capability.FUND_PROFILE, symbol, tls_policy=ctx.tls_policy)
    raw = result.payload.get("profile")
    return FundProfileRow.model_validate(raw) if isinstance(raw, dict) else None


class _Side(BaseModel):
    """One side of a comparison. A stock carries fundamentals and revenue seasonality; an ETF has
    neither (no revenue, ROE or debt), and carries its own per-year returns and fund facts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: Symbol
    kind: Literal["stock", "etf"]
    risk: _RiskBundle
    annual: _AnnualBundle | None = None
    season: _SeasonBundle | None = None
    etf_rows: tuple[EtfAnnualRow, ...] = ()
    etf: _EtfExtras | None = None

    @property
    def complete(self) -> list[AnnualFundamentalsRow]:
        return [row for row in self.annual.raw_rows if row.complete] if self.annual else []

    @property
    def etf_complete(self) -> list[EtfAnnualRow]:
        return [row for row in self.etf_rows if row.complete]

    @property
    def unit(self) -> str:
        return self.annual.unit if self.annual else "—"

    def avg(self, pick: str) -> float | None:
        values = [getattr(r, pick) for r in self.complete if getattr(r, pick) is not None]
        return round(fmean(values), 2) if values else None

    def cagr_of(self, pick: str) -> float | None:
        rows = [r for r in self.complete if getattr(r, pick) is not None]
        if len(rows) < _MIN_POINTS:
            return None
        return _cagr(getattr(rows[0], pick), getattr(rows[-1], pick), len(rows) - 1)

    def latest_debt_ratio(self) -> float | None:
        return self.complete[-1].debt_ratio_pct if self.complete else None

    def avg_yield(self) -> float | None:
        if self.kind == "etf":
            values = [r.dividend_yield_pct for r in self.etf_complete if r.dividend_yield_pct]
            return round(fmean(values), 2) if values else None
        return self.avg("dividend_yield_pct")

    def dividend_stability(self) -> tuple[float | None, str]:
        """Share of complete years with a cash dividend, and a one-line description (years paid
        of years covered, how many year-over-year cuts). An ETF compares dividends on the
        split-adjusted basis, so a split year is not counted as a cut."""
        if self.kind == "etf":
            amounts = [r.cash_dividend_adjusted or 0.0 for r in self.etf_complete]
        else:
            amounts = [r.cash_dividend or 0.0 for r in self.complete]
        if not amounts:
            return None, "—"
        paid = [amount for amount in amounts if amount > 0]
        cuts = sum(1 for prev, cur in pairwise(paid) if cur < prev)
        return round(
            len(paid) / len(amounts) * 100, 0
        ), f"{len(paid)}/{len(amounts)} 年配息，{cuts} 次下調"

    def dividend_cuts(self) -> int:
        """Year-over-year falls in the cash dividend (split-adjusted for an ETF)."""
        if self.kind == "etf":
            amounts = [r.cash_dividend_adjusted or 0.0 for r in self.etf_complete]
        else:
            amounts = [r.cash_dividend or 0.0 for r in self.complete]
        paid = [amount for amount in amounts if amount > 0]
        return sum(1 for prev, cur in pairwise(paid) if cur < prev)

    def spread(self) -> float | None:
        summary = self.season.summary if self.season else None
        return summary.spread_pct if summary else None

    def seasonality_text(self) -> str:
        summary = self.season.summary if self.season else None
        if summary is None:
            return "資料不足"
        peak = "、".join(summary.peak_periods[:2])
        trough = "、".join(summary.trough_periods[:2])
        months = f"{peak} / {trough}"
        if summary.peak_quarter is None:
            return months
        return f"旺季 Q{summary.peak_quarter} / 淡季 Q{summary.trough_quarter}（{months}）"

    def total_return_cagr(self) -> float | None:
        """Annualised simple total return (price + dividends, not reinvested), complete years."""
        returns = [
            r.total_return_simple_pct
            for r in self.etf_complete
            if r.total_return_simple_pct is not None
        ]
        if not returns:
            return None
        growth = 1.0
        for pct in returns:
            growth *= 1 + pct / 100
        return round((growth ** (1 / len(returns)) - 1) * 100, 2) if growth > 0 else None

    def best_worst_text(self) -> str:
        rows = [r for r in self.etf_complete if r.price_return_pct is not None]
        if not rows:
            return "—"
        best = max(rows, key=lambda r: r.price_return_pct or 0.0)
        worst = min(rows, key=lambda r: r.price_return_pct or 0.0)
        return (
            f"最佳 {best.price_return_pct:.1f}%（{best.year}）"
            f" / 最差 {worst.price_return_pct:.1f}%（{worst.year}）"
        )


def _row(
    metric: str,
    values: tuple[float | None, float | None],
    *,
    suffix: str = "",
    higher_is_better: bool = True,
    text: tuple[str, str] | None = None,
) -> ComparisonRow:
    a, b = values
    return ComparisonRow(
        metric=metric,
        a=text[0] if text else _fmt(a, suffix),
        b=text[1] if text else _fmt(b, suffix),
        stronger=_pick(a, b, higher_is_better=higher_is_better),
        direction=_HIGHER if higher_is_better else _LOWER,
    )


def _stability_row(a: _Side, b: _Side) -> ComparisonRow:
    """Dividend stability: more years with a dividend wins; on a tie, fewer year-over-year cuts."""
    share_a, text_a = a.dividend_stability()
    share_b, text_b = b.dividend_stability()
    cuts_a, cuts_b = a.dividend_cuts(), b.dividend_cuts()
    if share_a is None or share_b is None:
        stronger: Literal["A", "B", "tie"] | None = None
    elif share_a != share_b:
        stronger = "A" if share_a > share_b else "B"
    elif cuts_a != cuts_b:
        stronger = "A" if cuts_a < cuts_b else "B"
    else:
        stronger = "tie"
    return ComparisonRow(
        metric="配息穩定性",
        a=text_a,
        b=text_b,
        stronger=stronger,
        direction="配息年數越多越佳；相同時下調次數越少越佳",
    )


def _descriptive(metric: str, a: str, b: str) -> ComparisonRow:
    """A row that is shown but never judged (no better/worse exists for it)."""
    return ComparisonRow(metric=metric, a=a, b=b, stronger=None, direction="描述性，不評高低")


def _aum_text(side: _Side) -> str:
    if side.etf is None or side.etf.aum is None:
        return "—"
    if side.symbol.market is Market.TW:
        return f"約 {side.etf.aum / 1e8:,.0f} 億元"
    return f"約 {side.etf.aum / 1e9:,.1f} 十億美元"


def _fee_text(extras: _EtfExtras) -> str:
    """Whatever part of the internal-fee disclosure the source actually carries — for several
    Taiwan ETFs it has the custody fee but not the management fee, so the total cannot be summed
    and a bare dash would hide the half that is known."""
    if extras.management_fee_pct is None and extras.custody_fee_pct is None:
        return "—"
    manage = _fmt(extras.management_fee_pct, "%")
    custody = _fmt(extras.custody_fee_pct, "%")
    return f"經理費 {manage} / 保管費 {custody}"


def _risk_rows(a: _Side, b: _Side) -> tuple[ComparisonRow, ...]:
    ma, mb = a.risk.metrics, b.risk.metrics
    return (
        _row("波動度 (Beta)", (ma.beta, mb.beta), higher_is_better=False),
        _row(
            "年化波動率",
            (ma.annualised_volatility_pct, mb.annualised_volatility_pct),
            suffix="%",
            higher_is_better=False,
        ),
        _row("最大回撤", (ma.max_drawdown_pct, mb.max_drawdown_pct), suffix="%"),
        _row("期間價格報酬（未含股利）", (ma.price_return_pct, mb.price_return_pct), suffix="%"),
    )


def _stock_rows(a: _Side, b: _Side) -> tuple[ComparisonRow, ...]:
    return (
        _row("平均殖利率", (a.avg_yield(), b.avg_yield()), suffix="%"),
        _stability_row(a, b),
        _row("營收 CAGR", (a.cagr_of("revenue"), b.cagr_of("revenue")), suffix="%"),
        _row("EPS 成長率（CAGR）", (a.cagr_of("eps"), b.cagr_of("eps")), suffix="%"),
        _descriptive("季節性波動", a.seasonality_text(), b.seasonality_text()),
        _row("淡旺季營收差異", (a.spread(), b.spread()), suffix="%", higher_is_better=False),
        _row("平均 ROE", (a.avg("roe_pct"), b.avg("roe_pct")), suffix="%"),
        _row(
            "負債比（最新完整年度）",
            (a.latest_debt_ratio(), b.latest_debt_ratio()),
            suffix="%",
            higher_is_better=False,
        ),
        *_risk_rows(a, b),
    )


def _etf_rows(a: _Side, b: _Side) -> tuple[ComparisonRow, ...]:
    """The ETF-to-ETF table: what distinguishes two funds (cost, size, yield, total return, premium,
    concentration) — not a stock's revenue, EPS, ROE or debt, which an ETF does not have."""
    ea, eb = a.etf or _EtfExtras(), b.etf or _EtfExtras()
    return (
        _descriptive("追蹤指數、類別", ea.tracked or "—", eb.tracked or "—"),
        _descriptive("基金規模（AUM）", _aum_text(a), _aum_text(b)),
        _row(
            "費用率（經理費與保管費，下限近似）",
            (ea.expense_pct, eb.expense_pct),
            suffix="%",
            higher_is_better=False,
        ),
        _descriptive(
            "已揭露的內扣費用（經理費 / 保管費）",
            _fee_text(ea),
            _fee_text(eb),
        ),
        _descriptive(
            "最新折溢價（預估）",
            _fmt(ea.premium_pct, "%"),
            _fmt(eb.premium_pct, "%"),
        ),
        _row("平均殖利率", (a.avg_yield(), b.avg_yield()), suffix="%"),
        _stability_row(a, b),
        _row("含息年化報酬（概估）", (a.total_return_cagr(), b.total_return_cagr()), suffix="%"),
        _descriptive("年度報酬區間（價格）", a.best_worst_text(), b.best_worst_text()),
        _row("近 1 年累計報酬", (ea.return_1y, eb.return_1y), suffix="%"),
        _row("近 3 年累計報酬", (ea.return_3y, eb.return_3y), suffix="%"),
        _descriptive(
            "持股集中度（前十大合計權重）",
            _fmt(ea.top10_weight_pct, "%", 1),
            _fmt(eb.top10_weight_pct, "%", 1),
        ),
        *_risk_rows(a, b),
    )


def _mixed_rows(a: _Side, b: _Side) -> tuple[ComparisonRow, ...]:
    """A stock against an ETF share only the metrics both have."""
    return (
        _row("平均殖利率", (a.avg_yield(), b.avg_yield()), suffix="%"),
        _stability_row(a, b),
        *_risk_rows(a, b),
    )


class GetComparisonMetricsTool(BaseTool):
    """`get_comparison_metrics` — the side-by-side table for two stocks or two ETFs."""

    name = "get_comparison_metrics"
    description = (
        "Get a pre-computed side-by-side comparison of TWO stocks, or TWO ETFs, over ~5 years. "
        "Stocks: average dividend yield, dividend stability, revenue CAGR, EPS CAGR, seasonality, "
        "peak-trough revenue spread, average ROE, debt ratio, Beta, maximum drawdown, volatility, "
        "price return. ETFs get their OWN metrics (no revenue/ROE/debt): tracked index, fund size, "
        "expense ratio, premium/discount, yield, dividend stability, annualised total return, "
        "best/worst year, 1/3/5-year returns, holdings concentration (US), Beta, drawdown, "
        "volatility. A stock-vs-ETF pair shares only yield, stability and risk. Each row says "
        "which side is stronger on THAT metric and the rule; '—' means not available. A rebased "
        "price chart renders automatically."
    )
    input_model: type[BaseModel] = ComparisonInput
    output_model: type[BaseModel] = GetComparisonMetricsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "comparison_metrics"

    def __init__(self, router: DataAccessRouter, store: ParquetStore | None = None) -> None:
        self._router = router
        self._store = store

    async def _side(self, ctx: ToolContext, symbol: Symbol, years: int) -> _Side:
        profile: FundProfileRow | None = None
        if symbol.market is Market.TW:
            is_etf = bool(_TW_ETF_CODE.match(symbol.id))
        else:
            profile = await _fund_profile(self._router, ctx, symbol)
            is_etf = profile is not None
        if is_etf:
            (rows, _), extras, risk = await asyncio.gather(
                _load_etf_annual(self._router, ctx, symbol, years),
                _load_etf_extras(self._router, self._store, ctx, symbol, profile),
                _load_risk(self._router, ctx, symbol, years),
            )
            return _Side(symbol=symbol, kind="etf", risk=risk, etf_rows=rows, etf=extras)
        annual, season, risk = await asyncio.gather(
            _load_annual(self._router, ctx, symbol, years),
            _load_seasonality(self._router, ctx, symbol, years),
            _load_risk(self._router, ctx, symbol, years),
        )
        return _Side(symbol=symbol, kind="stock", risk=risk, annual=annual, season=season)

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(ComparisonInput, args)
        a, b = await asyncio.gather(
            self._side(ctx, Symbol(id=typed.id_a, market=typed.market_a), typed.years),
            self._side(ctx, Symbol(id=typed.id_b, market=typed.market_b), typed.years),
        )
        if a.kind == "etf" and b.kind == "etf":
            rows, table = _etf_rows(a, b), "etf"
        elif a.kind == "stock" and b.kind == "stock":
            rows, table = _stock_rows(a, b), "stock"
        else:
            rows, table = _mixed_rows(a, b), "mixed"
        notes = [
            "「優勢方」只表示該單一指標數值較佳（方向見 direction），不代表整體優劣或買賣建議。",
            f"Beta 的大盤代理：A={a.risk.benchmark}；B={b.risk.benchmark}。"
            "價格報酬為收盤價報酬，已還原分割、未含股利再投入。",
            "最大回撤為負值，越接近 0 越佳。",
        ]
        if table == "etf":
            notes.append(
                "ETF 沒有營收、EPS、ROE 與負債比，因此改比較費用、規模、折溢價、配息與含息報酬；"
                "含息年化報酬為價格報酬加當年現金股利（未再投入）的概估，殖利率與配息下調次數以分割還原後的"
                "每股股利計算。「—」代表本系統沒有該資料（例如美股 ETF 未提供費用率與折溢價）。"
            )
        elif table == "mixed":
            notes.append(
                "股票與 ETF 的財務結構不同，只比較兩者共有的指標：殖利率、配息穩定性與風險。"
            )
        points = tuple(
            SeriesPoint(year=month, metric=side.symbol.id, value=value)
            for side in (a, b)
            for month, value in side.risk.indexed
        )
        return GetComparisonMetricsOutput(
            symbol_a=typed.id_a,
            symbol_b=typed.id_b,
            kind_a=a.kind,
            kind_b=b.kind,
            amount_unit_a=a.unit,
            amount_unit_b=b.unit,
            rows=rows,
            notes=tuple(notes),
            price_series=points,
            chart_hint=ChartHint(
                mark=Mark.LINE,
                title="價格走勢比較（起點 = 100）",
                data_key="price_series",
                x=_YEAR,
                y=_VALUE,
                color=_METRIC,
            )
            if points
            else None,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    # The ETF comparison also reads the ingested NAV / AUM partitions (Taiwan premium, fund size).
    store = ParquetStore(root=get_settings().parquet_root)
    return [
        (GetAnnualFundamentalsTool(router), False),
        (GetRevenueSeasonalityTool(router), False),
        (GetRiskMetricsTool(router), False),
        (GetEtfAnnualSummaryTool(router), False),
        (GetComparisonMetricsTool(router, store), False),
    ]


__all__ = [
    "ComparisonInput",
    "GetAnnualFundamentalsOutput",
    "GetAnnualFundamentalsTool",
    "GetComparisonMetricsOutput",
    "GetComparisonMetricsTool",
    "GetEtfAnnualSummaryOutput",
    "GetEtfAnnualSummaryTool",
    "GetRevenueSeasonalityOutput",
    "GetRevenueSeasonalityTool",
    "GetRiskMetricsOutput",
    "GetRiskMetricsTool",
    "build_registration_table",
]
