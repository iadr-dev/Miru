"""Typed shapes for the per-stock analysis report: a multi-year fundamentals table and a revenue
seasonality summary. Lowest layer on purpose — `analytics/` computes them and `tools/` serves them,
and neither may import the other.

Every figure is `None`, never a fabricated 0 or an estimate, when its own inputs are missing. The
rows are pre-computed server-side so the model cites them instead of hand-deriving ratios from
hundreds of raw statement lines (the failure class `MonthRevenueTrendPoint` documents).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class AnnualFundamentalsRow(BaseModel):
    """One fiscal year. `complete` is False for a year still in progress (TW: fewer than four
    reported quarters) — its flow figures are year-to-date and its YoY compares the same quarters
    of the prior year; ROE is left `None` because a partial-year ROE is not comparable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int
    label: str
    complete: bool
    quarters: int | None = None
    revenue: float | None = None
    revenue_yoy_pct: float | None = None
    gross_margin_pct: float | None = None
    operating_margin_pct: float | None = None
    net_income: float | None = None
    eps: float | None = None
    roe_pct: float | None = None
    debt_ratio_pct: float | None = None
    current_ratio: float | None = None
    free_cash_flow: float | None = None
    cash_dividend: float | None = None
    stock_dividend: float | None = None
    dividend_yield_pct: float | None = None
    year_end_close: float | None = None


class EtfAnnualRow(BaseModel):
    """One calendar year of an ETF, on split-adjusted prices. `price_return_pct` is year-end to
    year-end (the first year runs from the first available close); `total_return_simple_pct` adds
    the year's cash dividends, NOT reinvested, over the starting price. A year still in progress,
    or one without a prior year-end to measure from, is `complete=False` and gets no yield."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int
    label: str
    complete: bool
    start_close: float | None = None
    year_end_close: float | None = None
    price_return_pct: float | None = None
    cash_dividend_per_share: float | None = None
    # The same dividends restated on today's (split-adjusted) share basis — the only basis on which
    # one year's payout can be compared with another's across a split.
    cash_dividend_adjusted: float | None = None
    dividend_yield_pct: float | None = None
    total_return_simple_pct: float | None = None


class SeriesPoint(BaseModel):
    """One point of a long-format chart series (`metric` drives the colour channel)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    year: str
    metric: str
    value: float


class SeasonalityPeriod(BaseModel):
    """One calendar month (or fiscal quarter) of the seasonal index."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: int
    label: str
    index: float
    deviation_pct: float


class SeasonalitySummary(BaseModel):
    """What the seasonal index says. `deviation_pct` is `(index - 1) * 100`, where a period's index
    is its revenue divided by its own year's average period revenue, averaged across the complete
    years (so growth between years does not masquerade as seasonality)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    basis: Literal["monthly", "quarterly"]
    years_covered: int
    peak_periods: tuple[str, ...]
    trough_periods: tuple[str, ...]
    peak_quarter: int | None = None
    trough_quarter: int | None = None
    quarter_deviation_pct: dict[str, float] | None = None
    # (strongest period index - weakest period index) / weakest period index, in percent.
    spread_pct: float | None = None
    formula: str
    note: str | None = None


__all__ = [
    "AnnualFundamentalsRow",
    "EtfAnnualRow",
    "SeasonalityPeriod",
    "SeasonalitySummary",
    "SeriesPoint",
]
