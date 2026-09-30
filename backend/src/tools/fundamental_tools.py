"""Fundamental-category tools — the FinMind Fundamental-category datasets not already wired in
`market_tools.py` (`dividends`). `financials`/`balance_sheet`/`cash_flow_statement` (income
statement, balance sheet, cash-flow statement) are folded into one `get_financial_statement`
tool alongside a `statement_type` selector rather than three near-duplicate tools, matching the
same shared row shape their adapters already share
(`schemas.capabilities.FinancialStatementLine`). Corporate-action reference rows (capital
reduction, delisting, split, par-value change) are bundled into `get_corporate_action_events`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date as date_
from datetime import timedelta
from enum import StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import (
    CapitalReductionReferencePriceRow,
    DividendEvent,
    DividendResultRow,
    EODBar,
    FinancialStatementLine,
    MarketValueRow,
    MarketValueWeightRow,
    MonthRevenueRow,
    ParValueChangeRow,
    SplitPriceRow,
    StockDelistingRow,
)
from schemas.chart_hint import ChartHint, FieldDef, FieldType, Mark
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


# `fiscal_year` on `DividendEvent` is FinMind's own free-text label — plain "104年" for a
# whole-year dividend or "108年第1季" for a quarterly one (see `get_dividend_schedule`'s live
# payload). The leading 3-digit ROC year is always present and always the first token; the
# calendar year it maps to (`roc + 1911`) is what `_dividend_year_summary` buckets by, since the
# 股利政策 chart groups by calendar year, not by the ROC label verbatim.
def _roc_fiscal_year_to_calendar_year(fiscal_year: str) -> int | None:
    digits = ""
    for ch in fiscal_year:
        if ch.isdigit():
            digits += ch
        else:
            break
    if not digits:
        return None
    return int(digits) + 1911


class DividendYearSummary(BaseModel):
    """One calendar year's dividend total — the 股利政策 chart's per-year bar (cash+stock
    stacked) and the stats-strip's "latest year" figures, both derived from the same bucketing
    so the chart and the headline numbers can never disagree."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int
    cash_dividend: float
    stock_dividend: float
    total_dividend: float


def _dividend_year_summary(events: Sequence[DividendEvent]) -> tuple[DividendYearSummary, ...]:
    totals: dict[int, list[float]] = {}
    for event in events:
        year = _roc_fiscal_year_to_calendar_year(event.fiscal_year)
        if year is None:
            continue
        cash, stock = totals.setdefault(year, [0.0, 0.0])
        totals[year] = [cash + (event.cash_dividend or 0.0), stock + (event.stock_dividend or 0.0)]
    return tuple(
        DividendYearSummary(
            year=year, cash_dividend=cash, stock_dividend=stock, total_dividend=cash + stock
        )
        for year, (cash, stock) in sorted(totals.items())
    )


class DividendFillResult(BaseModel):
    """Whether one settled ex-dividend event's price recovered to `before_price` — 填息
    (cash/combined ex-dividend) or 填權 (stock-only) — before the stock's *next* ex-dividend
    event, or (for the most recent event) as of the latest available close. `filled` is `None`,
    never `False`, when there is not yet enough later price history to answer either way (the
    next event hasn't happened yet AND no later close has reached `before_price`) — an unresolved
    event is not the same fact as a failed one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trade_date: date_
    before_price: float
    filled: bool | None
    filled_date: date_ | None = None


def _dividend_fill_results(
    dividend_result_rows: Sequence[DividendResultRow], bars: Sequence[EODBar]
) -> tuple[DividendFillResult, ...]:
    ordered_rows = sorted(dividend_result_rows, key=lambda r: r.trade_date)
    ordered_bars = sorted(bars, key=lambda b: b.trade_date)
    results: list[DividendFillResult] = []
    for idx, row in enumerate(ordered_rows):
        window_end = ordered_rows[idx + 1].trade_date if idx + 1 < len(ordered_rows) else date_.max
        filled = False
        filled_date: date_ | None = None
        for bar in ordered_bars:
            if bar.trade_date <= row.trade_date or bar.trade_date >= window_end:
                continue
            if bar.close >= row.before_price:
                filled = True
                filled_date = bar.trade_date
                break
        # No later bar at all within the window (the next event hasn't happened yet and no
        # price history reaches past this event) means "not yet resolved", not "failed" —
        # surfaced as `None` per this model's own docstring.
        has_later_bar = any(row.trade_date < bar.trade_date < window_end for bar in ordered_bars)
        results.append(
            DividendFillResult(
                trade_date=row.trade_date,
                before_price=row.before_price,
                filled=filled if (filled or has_later_bar) else None,
                filled_date=filled_date,
            )
        )
    return tuple(results)


class DividendPolicyStats(BaseModel):
    """The stats-strip figures above the 股利政策 chart — 綜合股利/現金殖利率/配息率/填息成功率,
    all pre-computed server-side from `events`/`fill_results`/`latest_eps`/`latest_close` so the
    frontend never re-derives a payout ratio or fill-rate from raw rows itself (same "cite the
    pre-computed figure directly" convention `MonthRevenueTrendPoint` documents). Any figure is
    `None`, never a fabricated 0, when its own inputs are missing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    latest_year: int | None = None
    latest_year_total_dividend: float | None = None
    cash_dividend_yield_pct: float | None = None
    payout_ratio_pct: float | None = None
    fill_rate_pct: float | None = None
    fill_rate_sample_size: int = 0


def _dividend_policy_stats(
    year_summaries: Sequence[DividendYearSummary],
    fill_results: Sequence[DividendFillResult],
    latest_close: float | None,
    annual_eps_by_year: dict[int, float],
) -> DividendPolicyStats:
    if not year_summaries:
        return DividendPolicyStats()
    latest = year_summaries[-1]
    cash_yield = (
        (latest.cash_dividend / latest_close) * 100 if latest_close and latest_close > 0 else None
    )
    latest_eps = annual_eps_by_year.get(latest.year)
    payout_ratio = (
        (latest.total_dividend / latest_eps) * 100 if latest_eps and latest_eps > 0 else None
    )
    resolved = [r for r in fill_results if r.filled is not None]
    fill_rate = (sum(1 for r in resolved if r.filled) / len(resolved)) * 100 if resolved else None
    return DividendPolicyStats(
        latest_year=latest.year,
        latest_year_total_dividend=latest.total_dividend,
        cash_dividend_yield_pct=cash_yield,
        payout_ratio_pct=payout_ratio,
        fill_rate_pct=fill_rate,
        fill_rate_sample_size=len(resolved),
    )


class GetDividendPolicySummaryOutput(BaseModel):
    """A composite report bundling everything the 股利政策 card needs — the announced-schedule
    events (table rows), the per-year chart bars, the stats-strip figures, and the per-event
    fill results — in one call, rather than the frontend fanning out to `get_dividend_schedule`
    + `get_financial_statement` + a long-range price fetch and re-deriving payout/fill-rate
    itself. Mirrors `GetCorporateActionEventsOutput`'s bundling precedent (multiple capability
    fetches, one composite tool) and `MonthRevenueTrendPoint`'s "pre-compute, cite directly"
    convention."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    events: tuple[DividendEvent, ...] = Field(default_factory=tuple)
    year_summaries: tuple[DividendYearSummary, ...] = Field(default_factory=tuple)
    fill_results: tuple[DividendFillResult, ...] = Field(default_factory=tuple)
    stats: DividendPolicyStats = Field(default_factory=DividendPolicyStats)
    # Tier-1 `chart_hint` pilot (see `schemas/chart_hint.py`): this is the pie/donut chart's real
    # unlock — `donut-with-legend.tsx` already knows how to draw exactly this per-year cash+stock
    # composition, but was structurally unreachable from the chat pipeline (no `card_type` shape
    # maps to it). `data_key="year_summaries"` disambiguates which of this composite output's 3
    # row-lists (`events`/`year_summaries`/`fill_results`) the hint plots.
    chart_hint: ChartHint | None = None


class GetDividendPolicySummaryTool(BaseTool):
    """`get_dividend_policy_summary` — the 股利政策 card's one-call composite: announced dividend
    events (`Capability.DIVIDENDS`), annual EPS (`Capability.FINANCIALS`, `基本每股盈餘`), and a
    live bounded-range `Capability.PRICE_EOD` fetch (same live-per-symbol pattern
    `etf_overlap_tools.py`'s fund-comparison return calc already uses — not the ingest store's
    60-trading-day-capped `get_price_history`, since 填息成功率 needs the settled-result's own
    `trade_date` window, which can span years, not just the last 60 sessions). All three fetches
    share one bounded `[start_date, end_date]` window from `DateRangeInput` — callers pass e.g.
    a 10-year window to match the screenshot-reference design's "計算區間" selector; there is no
    server-side default range beyond what the underlying capabilities themselves return."""

    name = "get_dividend_policy_summary"
    description = (
        "Get a composite 股利政策 report for one stock: announced dividend events, per-year "
        "cash+stock totals, 現金殖利率/配息率/填息成功率 stats, and per-event fill results."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetDividendPolicySummaryOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "dividend_policy_summary"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        symbol = typed.to_symbol()
        params = typed.date_params()

        schedule_result, events = await fetch_rows(
            self._router,
            Capability.DIVIDENDS,
            symbol,
            tls_policy=ctx.tls_policy,
            model=DividendEvent,
            key="events",
            params=params,
        )
        _dividend_result_result, dividend_result_rows = await fetch_rows(
            self._router,
            Capability.DIVIDEND_RESULT,
            symbol,
            tls_policy=ctx.tls_policy,
            model=DividendResultRow,
            key="rows",
            params=params,
        )
        _financials_result, statement_lines = await fetch_rows(
            self._router,
            Capability.FINANCIALS,
            symbol,
            tls_policy=ctx.tls_policy,
            model=FinancialStatementLine,
            key="lines",
            params=params,
        )
        _price_result, bars = await fetch_rows(
            self._router,
            Capability.PRICE_EOD,
            symbol,
            tls_policy=ctx.tls_policy,
            model=EODBar,
            key="bars",
            params=params,
        )

        annual_eps_by_year: dict[int, float] = {}
        for line in statement_lines:
            if line.item != "EPS":
                continue
            annual_eps_by_year[line.period_end.year] = (
                annual_eps_by_year.get(line.period_end.year, 0.0) + line.value
            )

        year_summaries = _dividend_year_summary(events)
        fill_results = _dividend_fill_results(dividend_result_rows, bars)
        latest_close = max(bars, key=lambda b: b.trade_date).close if bars else None
        stats = _dividend_policy_stats(
            year_summaries, fill_results, latest_close, annual_eps_by_year
        )

        return GetDividendPolicySummaryOutput(
            source_id=schedule_result.source_id,
            currentness_caveat=schedule_result.currentness_caveat,
            health_status=schedule_result.health.status,
            events=events,
            year_summaries=year_summaries,
            fill_results=fill_results,
            stats=stats,
            chart_hint=ChartHint(
                mark=Mark.ARC,
                data_key="year_summaries",
                theta=FieldDef(field="total_dividend", type=FieldType.QUANTITATIVE),
                color=FieldDef(field="year", type=FieldType.NOMINAL),
            )
            if year_summaries
            else None,
        )


class MonthRevenueTrendPoint(BaseModel):
    """One filed month's revenue plus its pre-computed MoM/YoY change, cumulative-YoY, a
    same-calendar-month record flag, and (when available) that month's period-end closing price
    — so a skill asking for a "近期月營收趨勢" never forces the model to manually diff adjacent
    rows itself (the same failure class as `InstitutionalFlowRollingNet`: a live
    `earnings-quality` turn's reasoning trace showed the model hand-transcribing raw
    month-revenue rows into a markdown table and running out of response budget before writing
    any prose — the final answer had zero analysis). `mom_pct`/`yoy_pct`/`cumulative_yoy_pct` are
    `None` when the comparison month(s) are absent from `rows`, never a fabricated figure from a
    mismatched month. `period_end_close` is `None` when no `PRICE_EOD` bar exists on or before
    this month's last filed trading day (e.g. a symbol whose price history doesn't reach this far
    back) — never fabricated as an interpolated/estimated price."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    revenue_year: int
    revenue_month: int
    revenue: int
    mom_pct: float | None = None
    yoy_pct: float | None = None
    # Year-to-date cumulative revenue (Jan..this month) vs. the same YTD span last year — FinMind/
    # TW-retail convention's own "累計年增%" column (see the reference design this mirrors).
    # `None` before every month from January through this one has a filed row for both years.
    cumulative_yoy_pct: float | None = None
    # Whether `revenue` is a new high for this calendar month across every prior year `rows`
    # covers (the reference design's "同期新高" badge) — computed only within the fetched
    # window, so a narrower `start_date` naturally narrows the comparison history, same as every
    # other window-bounded figure in this tool.
    is_period_high: bool = False
    period_end_close: float | None = None


def _safe_pct_change(current: int, prior: int | None) -> float | None:
    if prior is None or prior == 0:
        return None
    return (current - prior) / prior


def _prior_calendar_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _cumulative_yoy(
    by_year_month: Mapping[tuple[int, int], int], year: int, month: int
) -> float | None:
    """`sum(Jan..month, year)` vs. the same Jan..month span the year before — `None` if any month
    in either span is missing from `by_year_month` (a partial YTD sum compared against a full one
    would silently understate/overstate the real cumulative change)."""
    months = range(1, month + 1)
    current_months = [by_year_month.get((year, m)) for m in months]
    prior_months = [by_year_month.get((year - 1, m)) for m in months]
    if any(v is None for v in current_months) or any(v is None for v in prior_months):
        return None
    current_sum = sum(cast("list[int]", current_months))
    prior_sum = sum(cast("list[int]", prior_months))
    return _safe_pct_change(current_sum, prior_sum)


def _period_end_close(bars: Sequence[EODBar], on_or_before: date_) -> float | None:
    """The latest `PRICE_EOD` close at or before `on_or_before` — the price line's one point per
    filed month, matching that month's own last trading day rather than the filing date (which
    lands the following month, per `MonthRevenueRow.trade_date`'s own FinMind convention)."""
    candidates = [bar for bar in bars if bar.trade_date <= on_or_before]
    if not candidates:
        return None
    return max(candidates, key=lambda bar: bar.trade_date).close


def _month_revenue_trend(
    rows: Sequence[MonthRevenueRow], bars: Sequence[EODBar]
) -> tuple[MonthRevenueTrendPoint, ...]:
    ordered = sorted(rows, key=lambda row: (row.revenue_year, row.revenue_month))
    by_year_month = {(row.revenue_year, row.revenue_month): row.revenue for row in ordered}
    revenue_by_month: dict[int, list[tuple[int, int]]] = {}
    for row in ordered:
        revenue_by_month.setdefault(row.revenue_month, []).append((row.revenue_year, row.revenue))
    points: list[MonthRevenueTrendPoint] = []
    for row in ordered:
        prior_month_revenue = by_year_month.get(
            _prior_calendar_month(row.revenue_year, row.revenue_month)
        )
        prior_year_revenue = by_year_month.get((row.revenue_year - 1, row.revenue_month))
        same_calendar_month_years = revenue_by_month[row.revenue_month]
        is_period_high = row.revenue >= max(
            revenue for year, revenue in same_calendar_month_years if year <= row.revenue_year
        )
        # A month's own last filed trading day is one day before the next revenue period's
        # `trade_date` (the filing date FinMind stamps on the *following* month's 1st) — this
        # tool never assumes a fixed calendar-month-end, since `trade_date` is the real anchor
        # every other `MonthRevenueRow` reader in this codebase uses.
        month_end_anchor = row.trade_date - timedelta(days=1)
        points.append(
            MonthRevenueTrendPoint(
                revenue_year=row.revenue_year,
                revenue_month=row.revenue_month,
                revenue=row.revenue,
                mom_pct=_safe_pct_change(row.revenue, prior_month_revenue),
                yoy_pct=_safe_pct_change(row.revenue, prior_year_revenue),
                cumulative_yoy_pct=_cumulative_yoy(
                    by_year_month, row.revenue_year, row.revenue_month
                ),
                is_period_high=is_period_high,
                period_end_close=_period_end_close(bars, month_end_anchor),
            )
        )
    return tuple(points)


class GetMonthRevenueOutput(_RowsOutput):
    # `trend` is declared BEFORE `rows` — same field-order-survives-truncation reasoning as
    # `GetInstitutionalFlowOutput.rolling_net` — and, independent of truncation, gives the model
    # a pre-computed MoM/YoY figure per month so it never hand-diffs adjacent rows itself.
    trend: tuple[MonthRevenueTrendPoint, ...] = Field(default_factory=tuple)
    rows: tuple[MonthRevenueRow, ...] = Field(default_factory=tuple)
    # Tier-1 `chart_hint` pilot (see `schemas/chart_hint.py`): a discrete one-filing-per-month
    # magnitude series is exactly `Mark.BAR`'s intended shape — proves the generic chart pipe
    # renders this tool's real data equivalently to the hardcoded `card_type="month_revenue"` ->
    # `BarChart` path it already has, before any tool is migrated off that path.
    chart_hint: ChartHint | None = None


class GetMonthRevenueTool(BaseTool):
    """`get_month_revenue` — wraps `Capability.MONTH_REVENUE` (月營收 filing) plus a live bounded-
    range `Capability.PRICE_EOD` fetch (same live-per-symbol pattern
    `get_dividend_policy_summary`/`etf_overlap_tools.py` already use) for the price-line overlay.
    Also returns `trend` — server-side pre-computed MoM/YoY/cumulative-YoY change, same-period-
    high flag, and period-end close per filed month (see `MonthRevenueTrendPoint`'s own docstring
    for why)."""

    name = "get_month_revenue"
    description = (
        "Get monthly revenue (月營收) filing rows for one stock, plus a pre-computed MoM/YoY/"
        "cumulative-YoY change, same-period-high flag, and period-end closing price per month — "
        "cite `trend`'s figures directly rather than diffing `rows` yourself."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetMonthRevenueOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "month_revenue"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        symbol = typed.to_symbol()
        params = typed.date_params()
        result, rows = await fetch_rows(
            self._router,
            Capability.MONTH_REVENUE,
            symbol,
            tls_policy=ctx.tls_policy,
            model=MonthRevenueRow,
            key="rows",
            params=params,
        )
        _price_result, bars = await fetch_rows(
            self._router,
            Capability.PRICE_EOD,
            symbol,
            tls_policy=ctx.tls_policy,
            model=EODBar,
            key="bars",
            params=params,
        )
        return GetMonthRevenueOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
            trend=_month_revenue_trend(rows, bars),
            chart_hint=ChartHint(
                mark=Mark.BAR,
                x=FieldDef(field="trade_date", type=FieldType.TEMPORAL),
                y=FieldDef(field="revenue", type=FieldType.QUANTITATIVE),
            )
            if rows
            else None,
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
        (GetDividendPolicySummaryTool(router), False),
        (GetMonthRevenueTool(router), False),
        (GetMarketValueTool(router), False),
        (GetMarketValueWeightTool(router), False),
        (GetCorporateActionEventsTool(router), False),
    ]


__all__ = [
    "DividendFillResult",
    "DividendPolicyStats",
    "DividendYearSummary",
    "FinancialStatementType",
    "GetCorporateActionEventsOutput",
    "GetCorporateActionEventsTool",
    "GetDividendPolicySummaryOutput",
    "GetDividendPolicySummaryTool",
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
