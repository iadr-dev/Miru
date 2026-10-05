"""Assemble per-symbol fundamental ratios from raw financial-statement line items + market cap.

The ingested financial-statement datasets land as *raw line items* (one row per statement line
per reporting period), not pre-computed ratios — so ROE, debt/equity, gross profitability,
FCF yield, EV/EBITDA and friends are assembled here from the individual lines. Every ratio
degrades independently: a line item this symbol's filings do not carry yields `None` for the
ratios that need it, never a fabricated stand-in, and the score module drops just that sub-metric
rather than the whole factor.

**Reporting granularity.** Taiwan statutory filings are quarterly. Flow metrics (net income,
revenue, gross profit, operating cash flow, capex, operating income) are summed over the trailing
four quarters (TTM) when four are present; with fewer, the most recent quarter is annualized (x4)
and that approximation is disclosed via `ttm_is_annualized`. Stock metrics (equity, total assets,
total liabilities, cash) are point-in-time — the most recent reported period's value, never
summed. Earnings variability is the dispersion of the per-quarter net-income series itself.

**Data-access seam.** This module reads whole-market-ingested derived-store data through the same
read-only Parquet reader the rest of the derived-compute layer uses (a structural `Protocol` here
so it stays unit-testable without a live store) — never a live per-symbol source fetch. Financial
statements and market-cap snapshots are whole-market ingest datasets, so they are read back from
the derived store exactly as price/flow already are, not fetched per-symbol through the request
router.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as date_
from typing import Protocol

from schemas.capabilities import FinancialStatementLine, MarketValueRow, MonthRevenueRow
from schemas.enums import Market

# FinMind dataset identifiers for the whole-market-ingested financial datasets. Stable upstream
# names; kept here as the read keys rather than importing the ingest job specs, so the compute
# layer does not depend on the scheduler's wiring.
FINANCIALS_DATASET = "TaiwanStockFinancialStatements"  # income statement
BALANCE_SHEET_DATASET = "TaiwanStockBalanceSheet"
CASH_FLOW_DATASET = "TaiwanStockCashFlowsStatement"
MARKET_VALUE_DATASET = "TaiwanStockMarketValue"
MONTH_REVENUE_DATASET = "TaiwanStockMonthRevenue"

# Line-item name aliases — FinMind's normalized `type` column has varied over time and across the
# three statement families; a metric matches the first alias present rather than assuming one
# canonical spelling. A metric whose alias set finds nothing degrades to unavailable.
_NET_INCOME_ITEMS = (
    "IncomeAfterTaxes",
    "ProfitLossAttributableToOwnersOfParent",
    "NetIncome",
    "ProfitAfterTax",
)
_GROSS_PROFIT_ITEMS = ("GrossProfit", "GrossProfitLoss")
_REVENUE_ITEMS = ("Revenue", "OperatingRevenue", "NetRevenue")
_OPERATING_INCOME_ITEMS = ("OperatingIncome", "OperatingIncomeLoss")
_PRETAX_INCOME_ITEMS = ("PreTaxIncome", "IncomeBeforeIncomeTax", "ProfitBeforeTax")
_TAX_ITEMS = ("IncomeTaxExpense", "IncomeTax", "TAX")
_EQUITY_ITEMS = (
    "Equity",
    "TotalEquity",
    "EquityAttributableToOwnersOfParent",
    "StockholdersEquity",
)
_TOTAL_ASSETS_ITEMS = ("TotalAssets",)
_TOTAL_LIABILITIES_ITEMS = ("TotalLiabilities", "Liabilities")
_CASH_ITEMS = ("CashAndCashEquivalents", "CashAndCashEquivalent")
_OCF_ITEMS = (
    "CashFlowsFromOperatingActivities",
    "NetCashProvidedByOperatingActivities",
    "CashProvidedByOperatingActivities",
)
_CAPEX_ITEMS = (
    "AcquisitionOfPropertyPlantAndEquipment",
    "PurchaseOfPropertyPlantAndEquipment",
    "PropertyAndPlantAndEquipment",
    "CapitalExpenditures",
)
_DEPRECIATION_ITEMS = (
    "DepreciationAndAmortisation",
    "DepreciationAndAmortization",
    "Depreciation",
    "DepreciationExpense",
)
# Interest-bearing debt is not a single FinMind line; sum whichever borrowing components are
# present. If none are present, the debt term (and anything needing it) degrades — never a
# total-liabilities substitute, which would misstate enterprise value by folding in operating
# liabilities.
_DEBT_ITEMS = (
    "ShortTermBorrowings",
    "ShortTermBorrowing",
    "LongTermBorrowings",
    "LongTermLiabilitiesCurrentPortion",
    "BondsPayable",
    "CurrentPortionOfLongTermBorrowings",
)

_MIN_QUARTERS_FOR_TTM = 4
_ANNUALIZATION_FACTOR = 4.0
_MIN_QUARTERS_FOR_VARIABILITY = 2


class DerivedStoreReader(Protocol):
    """The one read-only operation `load_*` needs from the derived store — a structural seam so
    the fundamentals path is unit-testable with a fake reader and never hard-binds the concrete
    Parquet reader class."""

    def read_range(
        self,
        dataset: str,
        market: str,
        run_dates: Sequence[date_],
        *,
        symbols: Sequence[str] | None = None,
    ) -> list[dict[str, object]]: ...


@dataclass(frozen=True)
class FundamentalMetrics:
    """Raw (un-normalized) per-symbol fundamental sub-metrics. Every field is `None` when the
    line items it needs are absent for this symbol — the score module z-scores and blends only
    the non-`None` ones. `ttm_is_annualized` flags that flow metrics rest on a single annualized
    quarter rather than a full trailing-four-quarter sum, so the caller can disclose it."""

    roe: float | None = None
    debt_to_equity: float | None = None
    earnings_variability: float | None = None
    roic: float | None = None
    gross_profitability: float | None = None
    ev_ebitda: float | None = None
    fcf_yield: float | None = None
    pe: float | None = None
    pb: float | None = None
    gross_margin: float | None = None
    ttm_is_annualized: bool = False


def _project(row: dict[str, object], fields: frozenset[str]) -> dict[str, object]:
    """Keep only the model's own fields. The read-only reader globs with Hive partitioning on, so
    each row also carries the partition-derived `dataset`/`market`/`date`/`v` columns — the frozen
    `extra="forbid"` models would reject those, so project them out before validating."""
    return {key: value for key, value in row.items() if key in fields}


_STATEMENT_FIELDS = frozenset(FinancialStatementLine.model_fields)
_MARKET_VALUE_FIELDS = frozenset(MarketValueRow.model_fields)


def parse_statement_lines(rows: Sequence[dict[str, object]]) -> list[FinancialStatementLine]:
    """Validate raw derived-store dicts into `FinancialStatementLine`s, skipping any row that
    does not fit the shape (a defensive parse, not a silent data fabrication)."""
    lines: list[FinancialStatementLine] = []
    for row in rows:
        try:
            lines.append(FinancialStatementLine.model_validate(_project(row, _STATEMENT_FIELDS)))
        except ValueError:
            continue
    return lines


def parse_market_value_rows(rows: Sequence[dict[str, object]]) -> list[MarketValueRow]:
    values: list[MarketValueRow] = []
    for row in rows:
        try:
            values.append(MarketValueRow.model_validate(_project(row, _MARKET_VALUE_FIELDS)))
        except ValueError:
            continue
    return values


def _item_value(lines: Sequence[FinancialStatementLine], names: Sequence[str]) -> float | None:
    for line in lines:
        if line.item in names:
            return line.value
    return None


def _sum_present(lines: Sequence[FinancialStatementLine], names: Sequence[str]) -> float | None:
    matched = [line.value for line in lines if line.item in names]
    return sum(matched) if matched else None


def _periods_desc(lines: Sequence[FinancialStatementLine]) -> list[date_]:
    return sorted({line.period_end for line in lines}, reverse=True)


def _lines_for_period(
    lines: Sequence[FinancialStatementLine], period_end: date_
) -> list[FinancialStatementLine]:
    return [line for line in lines if line.period_end == period_end]


def _quarterly_series(lines: Sequence[FinancialStatementLine], names: Sequence[str]) -> list[float]:
    """The per-period values of one flow line item, newest period first."""
    series: list[float] = []
    for period in _periods_desc(lines):
        value = _item_value(_lines_for_period(lines, period), names)
        if value is not None:
            series.append(value)
    return series


def _ttm_flow(
    lines: Sequence[FinancialStatementLine], names: Sequence[str]
) -> tuple[float | None, bool]:
    """Trailing-four-quarter sum of a flow item, or the latest quarter annualized (x4) when fewer
    than four are present. Returns `(value_or_None, was_annualized)`."""
    series = _quarterly_series(lines, names)
    if not series:
        return None, False
    if len(series) >= _MIN_QUARTERS_FOR_TTM:
        return sum(series[:_MIN_QUARTERS_FOR_TTM]), False
    return series[0] * _ANNUALIZATION_FACTOR, True


def _latest_balance(lines: Sequence[FinancialStatementLine], names: Sequence[str]) -> float | None:
    """A point-in-time balance-sheet item at the most recent reported period (never summed)."""
    for period in _periods_desc(lines):
        value = _item_value(_lines_for_period(lines, period), names)
        if value is not None:
            return value
    return None


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def compute_fundamental_metrics(
    lines: Sequence[FinancialStatementLine],
    market_value: float | None,
) -> FundamentalMetrics:
    """Assemble every fundamental sub-metric that the supplied line items (across income/balance-
    sheet/cash-flow statements) plus `market_value` support. Missing inputs degrade only the
    ratios that need them."""
    net_income_ttm, ni_annualized = _ttm_flow(lines, _NET_INCOME_ITEMS)
    gross_profit_ttm, gp_annualized = _ttm_flow(lines, _GROSS_PROFIT_ITEMS)
    operating_income_ttm, oi_annualized = _ttm_flow(lines, _OPERATING_INCOME_ITEMS)
    pretax_ttm, _pt_annualized = _ttm_flow(lines, _PRETAX_INCOME_ITEMS)
    tax_ttm, _tax_annualized = _ttm_flow(lines, _TAX_ITEMS)
    ocf_ttm, ocf_annualized = _ttm_flow(lines, _OCF_ITEMS)
    capex_ttm, capex_annualized = _ttm_flow(lines, _CAPEX_ITEMS)
    depreciation_ttm, _dep_annualized = _ttm_flow(lines, _DEPRECIATION_ITEMS)

    equity = _latest_balance(lines, _EQUITY_ITEMS)
    total_assets = _latest_balance(lines, _TOTAL_ASSETS_ITEMS)
    total_liabilities = _latest_balance(lines, _TOTAL_LIABILITIES_ITEMS)
    cash = _latest_balance(lines, _CASH_ITEMS)
    total_debt = _sum_present(_latest_period_lines(lines), _DEBT_ITEMS)

    ttm_is_annualized = any(
        (ni_annualized, gp_annualized, oi_annualized, ocf_annualized, capex_annualized)
    )

    roe = _safe_ratio(net_income_ttm, equity)
    debt_to_equity = _safe_ratio(total_liabilities, equity)
    gross_profitability = _safe_ratio(gross_profit_ttm, total_assets)
    earnings_variability = _earnings_variability(lines)
    roic = _roic(
        _RoicInputs(
            operating_income=operating_income_ttm,
            pretax=pretax_ttm,
            tax=tax_ttm,
            total_debt=total_debt,
            equity=equity,
            cash=cash,
        )
    )

    ebitda = _ebitda(operating_income_ttm, depreciation_ttm)
    enterprise_value = _enterprise_value(market_value, total_debt, cash)
    ev_ebitda = _safe_ratio(enterprise_value, ebitda)
    fcf_yield = _fcf_yield(ocf_ttm, capex_ttm, market_value)
    pe = _safe_ratio(market_value, net_income_ttm)
    pb = _safe_ratio(market_value, equity)

    revenue_ttm, _rev_annualized = _ttm_flow(lines, _REVENUE_ITEMS)
    gross_margin = _safe_ratio(gross_profit_ttm, revenue_ttm)

    return FundamentalMetrics(
        roe=roe,
        debt_to_equity=debt_to_equity,
        earnings_variability=earnings_variability,
        roic=roic,
        gross_profitability=gross_profitability,
        ev_ebitda=ev_ebitda,
        fcf_yield=fcf_yield,
        pe=pe,
        pb=pb,
        gross_margin=gross_margin,
        ttm_is_annualized=ttm_is_annualized,
    )


_MONTHS_PER_YEAR = 12
_MONTH_REVENUE_FIELDS = frozenset(MonthRevenueRow.model_fields)


def parse_month_revenue_rows(rows: Sequence[dict[str, object]]) -> list[MonthRevenueRow]:
    """Validate raw derived-store dicts into `MonthRevenueRow`s, skipping any row that does not
    fit the shape (a defensive parse, not a silent data fabrication)."""
    values: list[MonthRevenueRow] = []
    for row in rows:
        try:
            values.append(MonthRevenueRow.model_validate(_project(row, _MONTH_REVENUE_FIELDS)))
        except ValueError:
            continue
    return values


def compute_revenue_yoy(rows: Sequence[MonthRevenueRow]) -> float | None:
    """Year-over-year change of the most recently filed month's revenue vs the same calendar
    month one year prior — `None` when either month is absent (never a fabricated comparison
    from a mismatched month)."""
    if not rows:
        return None
    latest = max(rows, key=lambda row: (row.revenue_year, row.revenue_month))
    prior_year = [
        row
        for row in rows
        if row.revenue_year == latest.revenue_year - 1 and row.revenue_month == latest.revenue_month
    ]
    if not prior_year:
        return None
    return _safe_ratio(latest.revenue - prior_year[0].revenue, prior_year[0].revenue)


def load_symbol_month_revenue(
    reader: DerivedStoreReader,
    *,
    market: Market,
    run_dates: Sequence[date_],
    symbol: str,
) -> list[MonthRevenueRow]:
    """Read this symbol's monthly-revenue filings back from the derived store across the given
    ingest-run-date partitions. Reads only the whole-market-ingested dataset; never a live
    per-symbol source call."""
    rows = reader.read_range(MONTH_REVENUE_DATASET, market.value, run_dates, symbols=[symbol])
    return [row for row in parse_month_revenue_rows(rows) if row.symbol == symbol]


def _latest_period_lines(lines: Sequence[FinancialStatementLine]) -> list[FinancialStatementLine]:
    periods = _periods_desc(lines)
    if not periods:
        return []
    return _lines_for_period(lines, periods[0])


def _earnings_variability(lines: Sequence[FinancialStatementLine]) -> float | None:
    """Coefficient of variation (stdev / |mean|) of the quarterly net-income series over the
    longest window available — a lower value is steadier earnings. `None` with fewer than two
    quarters, or when the mean is zero (an undefined ratio, never forced to a number)."""
    series = _quarterly_series(lines, _NET_INCOME_ITEMS)
    if len(series) < _MIN_QUARTERS_FOR_VARIABILITY:
        return None
    mean = statistics.fmean(series)
    if mean == 0:
        return None
    return statistics.pstdev(series) / abs(mean)


@dataclass(frozen=True)
class _RoicInputs:
    operating_income: float | None
    pretax: float | None
    tax: float | None
    total_debt: float | None
    equity: float | None
    cash: float | None


def _roic(inputs: _RoicInputs) -> float | None:
    """EBIT x (1 - effective tax rate) / invested capital, invested capital being total debt +
    equity - cash. Degrades to `None` whenever any input (notably interest-bearing debt, the
    most data-hungry term) is absent — the score then drops this sub-metric, not the factor."""
    if (
        inputs.operating_income is None
        or inputs.total_debt is None
        or inputs.equity is None
        or inputs.cash is None
    ):
        return None
    if inputs.pretax is None or inputs.tax is None or inputs.pretax == 0:
        return None
    effective_tax_rate = inputs.tax / inputs.pretax
    nopat = inputs.operating_income * (1.0 - effective_tax_rate)
    invested_capital = inputs.total_debt + inputs.equity - inputs.cash
    return _safe_ratio(nopat, invested_capital)


def _ebitda(operating_income: float | None, depreciation: float | None) -> float | None:
    if operating_income is None or depreciation is None:
        return None
    return operating_income + depreciation


def _enterprise_value(
    market_value: float | None, total_debt: float | None, cash: float | None
) -> float | None:
    if market_value is None or total_debt is None or cash is None:
        return None
    return market_value + total_debt - cash


def _fcf_yield(ocf: float | None, capex: float | None, market_value: float | None) -> float | None:
    """Free cash flow (operating cash flow less capex) over market cap. Capex is stored as a
    positive outflow magnitude in the ingested shape, so it is subtracted."""
    if ocf is None or capex is None:
        return None
    free_cash_flow = ocf - abs(capex)
    return _safe_ratio(free_cash_flow, market_value)


def load_symbol_financials(
    reader: DerivedStoreReader,
    *,
    market: Market,
    run_dates: Sequence[date_],
    symbol: str,
) -> list[FinancialStatementLine]:
    """Read this symbol's income/balance-sheet/cash-flow line items back from the derived store
    across the given ingest-run-date partitions, merged into one flat list. Reads only the
    whole-market-ingested datasets; never a live per-symbol source call."""
    lines: list[FinancialStatementLine] = []
    for dataset in (FINANCIALS_DATASET, BALANCE_SHEET_DATASET, CASH_FLOW_DATASET):
        rows = reader.read_range(dataset, market.value, run_dates, symbols=[symbol])
        lines.extend(line for line in parse_statement_lines(rows) if line.symbol == symbol)
    return lines


def load_symbol_market_value(
    reader: DerivedStoreReader,
    *,
    market: Market,
    run_dates: Sequence[date_],
    symbol: str,
) -> float | None:
    """The most recent ingested market-cap value for this symbol, or `None` if the market-value
    dataset was never ingested for it (the Sponsor-tier dataset may be absent) — the value factor
    then degrades gracefully rather than fabricating a capitalization."""
    rows = reader.read_range(MARKET_VALUE_DATASET, market.value, run_dates, symbols=[symbol])
    matched = [row for row in parse_market_value_rows(rows) if row.symbol == symbol]
    if not matched:
        return None
    latest = max(matched, key=lambda row: row.trade_date)
    return float(latest.market_value)


__all__ = [
    "BALANCE_SHEET_DATASET",
    "CASH_FLOW_DATASET",
    "FINANCIALS_DATASET",
    "MARKET_VALUE_DATASET",
    "MONTH_REVENUE_DATASET",
    "DerivedStoreReader",
    "FundamentalMetrics",
    "compute_fundamental_metrics",
    "compute_revenue_yoy",
    "load_symbol_financials",
    "load_symbol_market_value",
    "load_symbol_month_revenue",
    "parse_market_value_rows",
    "parse_month_revenue_rows",
    "parse_statement_lines",
]
