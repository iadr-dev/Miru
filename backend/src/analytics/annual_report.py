"""Pure computation behind the per-stock analysis report: multi-year fundamentals rolled up from
raw statement lines, per-year dividends and yield, and a revenue seasonality index.

No I/O and no clock. Statement item names differ by source — FinMind (TW) reports single-quarter
income lines and *year-to-date cumulative* cash-flow lines, yfinance (US) reports annual frames with
Yahoo's own item names — so each market has its own item map and rollup; the maths on top is shared.

Nothing here invents a number: a ratio is `None` when any input is absent, a year is flagged
incomplete rather than annualised, and seasonality is `None` with a reason when fewer than two
complete years exist.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date as date_
from statistics import fmean

from schemas.annual_report import (
    AnnualFundamentalsRow,
    EtfAnnualRow,
    SeasonalityPeriod,
    SeasonalitySummary,
)
from schemas.capabilities import DividendEvent, EODBar, FinancialStatementLine, MonthRevenueRow

_QUARTER_END_MONTHS = (3, 6, 9, 12)
_MIN_YEARS_FOR_SEASONALITY = 2
_MONTHS_IN_YEAR = 12
_QUARTERS_IN_YEAR = 4
_TOP_PERIODS = 3

# ---- TW (FinMind) item names -----------------------------------------------------------------
_TW_REVENUE = "Revenue"
_TW_GROSS = "GrossProfit"
_TW_OPERATING = "OperatingIncome"
# On the income statement FinMind's `EquityAttributableToOwnersOfParent` is the net income
# attributable to the parent; on the balance sheet the same name is the equity.
_TW_NET_INCOME_PARENT = "EquityAttributableToOwnersOfParent"
_TW_EPS = "EPS"
_TW_ASSETS = "TotalAssets"
_TW_LIABILITIES = "Liabilities"
_TW_CURRENT_ASSETS = "CurrentAssets"
_TW_CURRENT_LIABILITIES = "CurrentLiabilities"
_TW_EQUITY_PARENT = "EquityAttributableToOwnersOfParent"
_TW_OCF = ("CashFlowsFromOperatingActivities", "NetCashInflowFromOperatingActivities")
_TW_CAPEX = "PropertyAndPlantAndEquipment"  # investing outflow, reported negative

# ---- US (yfinance) item names ----------------------------------------------------------------
_US_REVENUE = ("Total Revenue", "Operating Revenue")
_US_GROSS = "Gross Profit"
_US_OPERATING = "Operating Income"
_US_NET_INCOME = ("Net Income Common Stockholders", "Net Income")
_US_EPS = ("Diluted EPS", "Basic EPS")
_US_ASSETS = "Total Assets"
_US_LIABILITIES = ("Total Liabilities Net Minority Interest", "Total Liabilities")
_US_CURRENT_ASSETS = "Current Assets"
_US_CURRENT_LIABILITIES = "Current Liabilities"
_US_EQUITY = ("Stockholders Equity", "Common Stock Equity")
_US_OCF = ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
_US_CAPEX = "Capital Expenditure"
_US_FCF = "Free Cash Flow"


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * 100


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _growth_pct(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior <= 0:
        return None
    return (current - prior) / prior * 100


def _first(values: Mapping[str, float], names: Sequence[str]) -> float | None:
    for name in names:
        if name in values:
            return values[name]
    return None


def _by_period(
    lines: Sequence[FinancialStatementLine], *, period_type: str | None = None
) -> dict[date_, dict[str, float]]:
    """`{period_end: {item: value}}`, optionally restricted to one `period_type`."""
    out: dict[date_, dict[str, float]] = defaultdict(dict)
    for line in lines:
        if period_type is not None and line.period_type != period_type:
            continue
        out[line.period_end][line.item] = line.value
    return out


def _is_quarter_end(day: date_) -> bool:
    return day.month in _QUARTER_END_MONTHS


# ---- TW --------------------------------------------------------------------------------------


def _tw_year_income(
    quarters: Mapping[date_, Mapping[str, float]],
) -> dict[int, dict[str, float | int]]:
    """Sum the single-quarter income lines per calendar year, with the quarter count."""
    years: dict[int, dict[str, float | int]] = {}
    for period_end in sorted(quarters):
        if not _is_quarter_end(period_end):
            continue
        items = quarters[period_end]
        bucket = years.setdefault(period_end.year, {"quarters": 0})
        bucket["quarters"] = int(bucket["quarters"]) + 1
        for key in (_TW_REVENUE, _TW_GROSS, _TW_OPERATING, _TW_NET_INCOME_PARENT, _TW_EPS):
            if key in items:
                bucket[key] = float(bucket.get(key, 0.0)) + items[key]
    return years


def _tw_year_end(
    quarters: Mapping[date_, Mapping[str, float]], year: int
) -> Mapping[str, float] | None:
    """The December (Q4) line set of `year`, or its latest reported quarter for a year still in
    progress. Balance-sheet lines are point-in-time and cash-flow lines are year-to-date, so the
    latest quarter is the right one for both."""
    candidates = [day for day in quarters if day.year == year and _is_quarter_end(day)]
    return quarters[max(candidates)] if candidates else None


def build_tw_annual_rows(
    income: Sequence[FinancialStatementLine],
    balance: Sequence[FinancialStatementLine],
    cash_flow: Sequence[FinancialStatementLine],
    *,
    years: int,
) -> list[AnnualFundamentalsRow]:
    """Roll FinMind's quarterly lines up to fiscal (= calendar) years, newest `years` years.

    Revenue, gross profit, operating income, net income and EPS are summed over the year's
    quarters. ROE divides the parent's net income by the average of the prior and current year-end
    parent equity (year-end alone when there is no prior year). Debt ratio and current ratio are
    year-end balance-sheet ratios. Free cash flow is operating cash flow less capex at Q4 (FinMind
    cash-flow lines are cumulative within the year). A year with fewer than four reported quarters
    is partial: YTD figures, YoY against the same quarters of the prior year, no ROE."""
    income_q = _by_period(income)
    balance_q = _by_period(balance)
    cash_q = _by_period(cash_flow)
    by_year = _tw_year_income(income_q)
    rows = [
        _tw_row(year, by_year, income_q=income_q, balance_q=balance_q, cash_q=cash_q)
        for year in sorted(by_year)
    ]
    return rows[-years:]


def _tw_prior_same_quarters(
    income_q: Mapping[date_, Mapping[str, float]],
    by_year: Mapping[int, object],
    year: int,
    count: int,
    key: str,
) -> float | None:
    """`key` summed over the first `count` reported quarters of `year - 1`, or None when that year
    is absent or has fewer than `count` quarters carrying the item."""
    if year - 1 not in by_year:
        return None
    total = 0.0
    found = 0
    for period_end in sorted(income_q):
        if period_end.year != year - 1 or not _is_quarter_end(period_end):
            continue
        if found >= count:
            break
        if key in income_q[period_end]:
            total += income_q[period_end][key]
            found += 1
    return total if found == count else None


def _item(items: Mapping[str, float] | None, key: str) -> float | None:
    return _f(items.get(key)) if items else None


def _average_equity(equity_end: float | None, equity_prior: float | None) -> float | None:
    if equity_end is not None and equity_prior is not None:
        return (equity_end + equity_prior) / 2
    return equity_end


def _tw_free_cash_flow(end_cash: Mapping[str, float] | None) -> float | None:
    ocf = _first(end_cash, _TW_OCF) if end_cash else None
    capex = _item(end_cash, _TW_CAPEX)
    return (ocf + capex) if ocf is not None and capex is not None else None


def _tw_row(
    year: int,
    by_year: Mapping[int, Mapping[str, float | int]],
    *,
    income_q: Mapping[date_, Mapping[str, float]],
    balance_q: Mapping[date_, Mapping[str, float]],
    cash_q: Mapping[date_, Mapping[str, float]],
) -> AnnualFundamentalsRow:
    bucket = by_year[year]
    quarters = int(bucket["quarters"])
    complete = quarters == len(_QUARTER_END_MONTHS)
    revenue = _f(bucket.get(_TW_REVENUE))
    net_income = _f(bucket.get(_TW_NET_INCOME_PARENT))

    end_balance = _tw_year_end(balance_q, year)
    prior_balance = _tw_year_end(balance_q, year - 1)
    average_equity = _average_equity(
        _item(end_balance, _TW_EQUITY_PARENT), _item(prior_balance, _TW_EQUITY_PARENT)
    )

    return AnnualFundamentalsRow(
        year=year,
        label=str(year) if complete else f"{year} 前{quarters}季",
        complete=complete,
        quarters=quarters,
        revenue=revenue,
        revenue_yoy_pct=_growth_pct(
            revenue, _tw_prior_same_quarters(income_q, by_year, year, quarters, _TW_REVENUE)
        ),
        gross_margin_pct=_pct(_f(bucket.get(_TW_GROSS)), revenue),
        operating_margin_pct=_pct(_f(bucket.get(_TW_OPERATING)), revenue),
        net_income=net_income,
        eps=_f(bucket.get(_TW_EPS)),
        roe_pct=_pct(net_income, average_equity) if complete else None,
        debt_ratio_pct=_pct(_item(end_balance, _TW_LIABILITIES), _item(end_balance, _TW_ASSETS)),
        current_ratio=_ratio(
            _item(end_balance, _TW_CURRENT_ASSETS), _item(end_balance, _TW_CURRENT_LIABILITIES)
        ),
        free_cash_flow=_tw_free_cash_flow(_tw_year_end(cash_q, year)),
    )


# ---- US --------------------------------------------------------------------------------------


def build_us_annual_rows(
    income: Sequence[FinancialStatementLine],
    balance: Sequence[FinancialStatementLine],
    cash_flow: Sequence[FinancialStatementLine],
    *,
    years: int,
) -> list[AnnualFundamentalsRow]:
    """Annual frames straight from yfinance (keyed by fiscal-year-end date; Yahoo supplies roughly
    four of them, which is why `years` can exceed what comes back). YoY needs the prior year, so the
    oldest row has none; ROE averages the prior and current year's equity when both exist."""
    income_a = _by_period(income, period_type="annual")
    balance_a = _by_period(balance, period_type="annual")
    cash_a = _by_period(cash_flow, period_type="annual")

    rows: list[AnnualFundamentalsRow] = []
    previous_revenue: float | None = None
    previous_equity: float | None = None
    for period_end in sorted(income_a):
        items = income_a[period_end]
        bal = balance_a.get(period_end, {})
        cash = cash_a.get(period_end, {})
        revenue = _first(items, _US_REVENUE)
        gross = items.get(_US_GROSS)
        operating = items.get(_US_OPERATING)
        net_income = _first(items, _US_NET_INCOME)
        equity = _first(bal, _US_EQUITY)
        average_equity = (
            (equity + previous_equity) / 2
            if equity is not None and previous_equity is not None
            else equity
        )
        ocf = _first(cash, _US_OCF)
        capex = cash.get(_US_CAPEX)
        free_cash_flow = cash.get(_US_FCF)
        if free_cash_flow is None and ocf is not None and capex is not None:
            free_cash_flow = ocf + capex  # Yahoo reports capex as a negative outflow

        rows.append(
            AnnualFundamentalsRow(
                year=period_end.year,
                label=str(period_end.year),
                complete=True,
                revenue=revenue,
                revenue_yoy_pct=_growth_pct(revenue, previous_revenue),
                gross_margin_pct=_pct(gross, revenue),
                operating_margin_pct=_pct(operating, revenue),
                net_income=net_income,
                eps=_first(items, _US_EPS),
                roe_pct=_pct(net_income, average_equity),
                debt_ratio_pct=_pct(_first(bal, _US_LIABILITIES), bal.get(_US_ASSETS)),
                current_ratio=_ratio(bal.get(_US_CURRENT_ASSETS), bal.get(_US_CURRENT_LIABILITIES)),
                free_cash_flow=free_cash_flow,
            )
        )
        previous_revenue = revenue
        previous_equity = equity
    return rows[-years:]


def _f(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


# ---- dividends -------------------------------------------------------------------------------


def _roc_year(fiscal_year: str) -> int | None:
    digits = ""
    for char in fiscal_year:
        if char.isdigit():
            digits += char
        else:
            break
    return int(digits) + 1911 if digits else None


def attach_dividends(
    rows: Sequence[AnnualFundamentalsRow],
    events: Sequence[DividendEvent],
    bars: Sequence[EODBar],
    *,
    roc_fiscal_year: bool,
) -> list[AnnualFundamentalsRow]:
    """Add per-year cash/stock dividend per share and yield to each row.

    TW events carry a ROC fiscal-year string (「113年」 → 2024) and are bucketed by it; US events
    are per-payment ex-dividend amounts and are bucketed by ex-dividend year. Yield is the year's
    cash dividend over that year's last available close (the latest close for the current year), so
    it is a payout-time-independent, reproducible number — not a forward estimate."""
    cash: dict[int, float] = defaultdict(float)
    stock: dict[int, float] = defaultdict(float)
    for event in events:
        if roc_fiscal_year:
            year = _roc_year(event.fiscal_year)
        else:
            year = (event.cash_ex_dividend_date or event.announced_date).year
        if year is None:
            continue
        cash[year] += event.cash_dividend or 0.0
        stock[year] += event.stock_dividend or 0.0

    last_close: dict[int, float] = {}
    for bar in sorted(bars, key=lambda b: b.trade_date):
        last_close[bar.trade_date.year] = bar.close

    out: list[AnnualFundamentalsRow] = []
    for row in rows:
        year_cash = cash.get(row.year)
        close = last_close.get(row.year)
        out.append(
            row.model_copy(
                update={
                    "cash_dividend": year_cash,
                    "stock_dividend": stock.get(row.year),
                    "year_end_close": close,
                    # A year still in progress has only part of its dividends against today's
                    # price — that ratio would look like a yield cut, so it is left out.
                    "dividend_yield_pct": _pct(year_cash, close) if row.complete else None,
                }
            )
        )
    return out


# ---- ETF -------------------------------------------------------------------------------------


def build_etf_annual_rows(
    raw_closes: Sequence[tuple[date_, float]],
    adjusted_closes: Sequence[tuple[date_, float]],
    events: Sequence[DividendEvent],
    *,
    years: int,
    today: date_,
) -> list[EtfAnnualRow]:
    """Per-calendar-year price return, dividends and yield for an ETF.

    Returns use the split-adjusted series so a split is not a crash. A dividend is paid per share
    on the share basis of its ex-dividend date, so it is converted onto the adjusted basis with that
    day's adjusted/raw price ratio before it is compared with adjusted prices — otherwise a
    dividend paid before 0050's 4-for-1 split would look four times too large against today's
    prices. Dividends are attributed to the year of their ex-dividend date (when the holder
    actually receives them)."""
    raw = dict(sorted(raw_closes))
    adjusted = dict(sorted(adjusted_closes))
    if not adjusted:
        return []
    days = sorted(adjusted)

    year_end: dict[int, float] = {}
    for day in days:
        year_end[day.year] = adjusted[day]

    dividend_per_share, dividend_adjusted = _etf_dividends(events, days, raw, adjusted)
    prices = _EtfPrices(days, adjusted, year_end)
    rows = [
        _etf_row(
            year,
            prices,
            today=today,
            per_share=dividend_per_share.get(year),
            paid=dividend_adjusted.get(year),
        )
        for year in sorted(year_end)
    ]
    return rows[-years:]


def _etf_adjustment_factor(
    day: date_, days: Sequence[date_], raw: Mapping[date_, float], adjusted: Mapping[date_, float]
) -> float:
    """Adjusted/raw price ratio on the last priced day on or before `day` (1.0 when there is none
    or its raw price is not positive)."""
    eligible = [d for d in days if d <= day]
    if not eligible or raw.get(eligible[-1], 0) <= 0:
        return 1.0
    return adjusted[eligible[-1]] / raw[eligible[-1]]


def _etf_dividends(
    events: Sequence[DividendEvent],
    days: Sequence[date_],
    raw: Mapping[date_, float],
    adjusted: Mapping[date_, float],
) -> tuple[dict[int, float], dict[int, float]]:
    """Cash dividends per ex-dividend year, as paid and on the adjusted share basis."""
    per_share: dict[int, float] = defaultdict(float)
    on_adjusted: dict[int, float] = defaultdict(float)
    for event in events:
        amount = event.cash_dividend or 0.0
        if amount <= 0:
            continue
        ex_date = event.cash_ex_dividend_date or event.announced_date
        per_share[ex_date.year] += amount
        on_adjusted[ex_date.year] += amount * _etf_adjustment_factor(ex_date, days, raw, adjusted)
    return per_share, on_adjusted


def _etf_label_suffix(*, complete: bool, year: int, today: date_) -> str:
    if complete:
        return ""
    return "（年初至今）" if year == today.year else "（部分年度）"


def _round_or_none(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


@dataclass(frozen=True)
class _EtfPrices:
    """The adjusted close series: its trading days (sorted), values, and each year's last close."""

    days: Sequence[date_]
    adjusted: Mapping[date_, float]
    year_end: Mapping[int, float]


def _etf_row(
    year: int, prices: _EtfPrices, *, today: date_, per_share: float | None, paid: float | None
) -> EtfAnnualRow:
    days = prices.days
    prior_end = prices.year_end.get(year - 1)
    start = prior_end if prior_end is not None else prices.adjusted[days[0]]
    end = prices.year_end[year]
    has_full_window = prior_end is not None or (year == days[0].year and days[0].month == 1)
    complete = year < today.year and has_full_window
    return EtfAnnualRow(
        year=year,
        label=f"{year}{_etf_label_suffix(complete=complete, year=year, today=today)}",
        complete=complete,
        start_close=round(start, 2),
        year_end_close=round(end, 2),
        price_return_pct=_round_or_none(_pct(end - start, start), 2),
        cash_dividend_per_share=_round_or_none(per_share, 4),
        cash_dividend_adjusted=_round_or_none(paid, 4),
        dividend_yield_pct=(
            round(_pct(paid, end) or 0.0, 2) if complete and paid is not None else None
        ),
        total_return_simple_pct=(
            round(_pct(end + (paid or 0.0) - start, start) or 0.0, 2) if complete else None
        ),
    )


# ---- seasonality -----------------------------------------------------------------------------

_MONTH_LABELS = tuple(f"{month}月" for month in range(1, 13))
_SEASONALITY_FORMULA = (
    "季節指數 = 該期營收 ÷ 該年各期平均營收，再對完整年度取平均；"
    "偏離% = (指數 - 1) x 100；淡旺季差異% = (最高期指數 - 最低期指數) ÷ 最低期指數 x 100"
)


def _seasonal_indices(
    by_year: Mapping[int, Mapping[int, float]], periods: int
) -> tuple[list[float] | None, int]:
    """Average, over complete years, of each period's revenue divided by its own year's mean
    period revenue. Returns `(indices, years_used)`; `(None, n)` with fewer than two full years."""
    ratios: dict[int, list[float]] = defaultdict(list)
    used = 0
    for revenues in by_year.values():
        if len(revenues) != periods:
            continue
        mean = fmean(revenues.values())
        if mean <= 0:
            continue
        used += 1
        for period, revenue in revenues.items():
            ratios[period].append(revenue / mean)
    if used < _MIN_YEARS_FOR_SEASONALITY:
        return None, used
    return [fmean(ratios[period]) for period in range(1, periods + 1)], used


def _summarise(
    indices: Sequence[float],
    labels: Sequence[str],
    *,
    basis: str,
    years_used: int,
    quarter_of: Callable[[int], int] | None,
) -> tuple[list[SeasonalityPeriod], SeasonalitySummary]:
    periods = [
        SeasonalityPeriod(
            period=i + 1,
            label=labels[i],
            index=round(idx, 4),
            deviation_pct=round((idx - 1) * 100, 2),
        )
        for i, idx in enumerate(indices)
    ]
    ordered = sorted(periods, key=lambda p: p.index, reverse=True)
    top = _TOP_PERIODS if basis == "monthly" else 1
    peak = tuple(p.label for p in ordered[:top])
    trough = tuple(p.label for p in ordered[-top:][::-1])
    lowest = ordered[-1].index
    spread = round((ordered[0].index - lowest) / lowest * 100, 1) if lowest > 0 else None

    quarter_dev: dict[str, float] | None = None
    peak_q: int | None = None
    trough_q: int | None = None
    if quarter_of is not None:
        totals: dict[int, list[float]] = defaultdict(list)
        for p in periods:
            totals[quarter_of(p.period)].append(p.index)
        quarter_means = {q: fmean(v) for q, v in totals.items()}
        base = fmean(quarter_means.values())
        quarter_dev = {
            f"Q{q}": round((m / base - 1) * 100, 2) for q, m in sorted(quarter_means.items())
        }
        peak_q = max(quarter_means, key=lambda q: quarter_means[q])
        trough_q = min(quarter_means, key=lambda q: quarter_means[q])

    summary = SeasonalitySummary(
        basis="monthly" if basis == "monthly" else "quarterly",
        years_covered=years_used,
        peak_periods=peak,
        trough_periods=trough,
        peak_quarter=peak_q,
        trough_quarter=trough_q,
        quarter_deviation_pct=quarter_dev,
        spread_pct=spread,
        formula=_SEASONALITY_FORMULA,
    )
    return periods, summary


def monthly_seasonality(
    rows: Sequence[MonthRevenueRow], *, years: int
) -> tuple[list[SeasonalityPeriod], SeasonalitySummary | None, str | None]:
    """Seasonal index from monthly revenue over the most recent `years` *complete* calendar years.
    Returns `(periods, summary, reason_if_unavailable)`."""
    by_year: dict[int, dict[int, float]] = defaultdict(dict)
    for row in rows:
        by_year[row.revenue_year][row.revenue_month] = float(row.revenue)
    complete = sorted(year for year, months in by_year.items() if len(months) == _MONTHS_IN_YEAR)[
        -years:
    ]
    indices, used = _seasonal_indices({y: by_year[y] for y in complete}, _MONTHS_IN_YEAR)
    if indices is None:
        return [], None, f"完整年度月營收只有 {used} 年，至少需要 {_MIN_YEARS_FOR_SEASONALITY} 年"
    periods, summary = _summarise(
        indices,
        _MONTH_LABELS,
        basis="monthly",
        years_used=used,
        quarter_of=lambda m: (m - 1) // 3 + 1,
    )
    return periods, summary, None


def quarterly_seasonality(
    income: Sequence[FinancialStatementLine], *, years: int
) -> tuple[list[SeasonalityPeriod], SeasonalitySummary | None, str | None]:
    """Seasonal index from quarterly revenue. US: Yahoo's quarterly frame spans only about five
    quarters — usually too few complete years, in which case this says so instead of guessing."""
    quarters = _by_period(income, period_type="quarterly")
    by_year: dict[int, dict[int, float]] = defaultdict(dict)
    for period_end, items in quarters.items():
        revenue = _first(items, _US_REVENUE)
        if revenue is None:
            continue
        by_year[period_end.year][(period_end.month - 1) // 3 + 1] = revenue
    complete = sorted(year for year, qs in by_year.items() if len(qs) == _QUARTERS_IN_YEAR)[-years:]
    indices, used = _seasonal_indices({y: by_year[y] for y in complete}, _QUARTERS_IN_YEAR)
    if indices is None:
        return (
            [],
            None,
            f"來源只提供 {used} 個完整年度的季營收，不足以判斷季節性"
            f"（至少需要 {_MIN_YEARS_FOR_SEASONALITY} 年）",
        )
    labels = tuple(f"Q{q}" for q in range(1, 5))
    periods, summary = _summarise(
        indices, labels, basis="quarterly", years_used=used, quarter_of=None
    )
    return (
        periods,
        summary.model_copy(
            update={
                "peak_quarter": _quarter_number(summary.peak_periods),
                "trough_quarter": _quarter_number(summary.trough_periods),
            }
        ),
        None,
    )


def _quarter_number(labels: Sequence[str]) -> int | None:
    return int(labels[0][1:]) if labels else None


__all__ = [
    "attach_dividends",
    "build_etf_annual_rows",
    "build_tw_annual_rows",
    "build_us_annual_rows",
    "monthly_seasonality",
    "quarterly_seasonality",
]
