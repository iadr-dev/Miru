"""Price-based risk and return metrics for the analysis report: Beta, annualised volatility, maximum
drawdown and multi-year price return. Pure functions over `(trade_date, close)` series — no I/O.

These are *price* returns (closing prices as the source reports them, dividends not reinvested), so
they are labelled that way wherever they are shown; a dividend-reinvested total return is a
different, source-specific number (`get_fund_comparison`). Beta is measured against a caller-chosen
market proxy over the dates both series share, using daily simple returns.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date as date_
from itertools import pairwise
from math import sqrt
from statistics import fmean, pstdev

_TRADING_DAYS_PER_YEAR = 252
_MIN_OBSERVATIONS = 30
_DAYS_PER_YEAR = 365.25

Series = Sequence[tuple[date_, float]]


def adjust_discontinuities(
    series: Series, *, max_abs_move: float
) -> tuple[list[tuple[date_, float]], list[date_]]:
    """Rebase prices across split-like breaks so they do not read as crashes.

    The ingested closes are unadjusted, so a stock split (0050's 4-for-1 in 2025) or a par-value
    change shows up as a one-day collapse that would wreck drawdown, volatility and Beta. Any
    day-over-day move larger than `max_abs_move` (a fraction) is treated as such a break: every
    earlier close is scaled so the move disappears. Taiwan limits a day's move to about 10%, so
    `0.105` catches every split there without touching a real move; US has no daily limit, so its
    threshold (0.45) only catches splits of 2:1 and larger and leaves a genuine crash alone. The
    break dates are returned so a report can say what was adjusted."""
    ordered = sorted(series, key=lambda point: point[0])
    adjusted = [close for _, close in ordered]
    breaks: list[date_] = []
    for i in range(1, len(ordered)):
        previous, current = ordered[i - 1][1], ordered[i][1]
        if previous <= 0 or current <= 0:
            continue
        if abs(current / previous - 1) > max_abs_move:
            factor = current / previous
            for j in range(i):
                adjusted[j] *= factor
            breaks.append(ordered[i][0])
    return [(day, adjusted[i]) for i, (day, _) in enumerate(ordered)], breaks


def _daily_returns(series: Series) -> dict[date_, float]:
    ordered = sorted(series, key=lambda point: point[0])
    out: dict[date_, float] = {}
    for (_, previous), (day, close) in pairwise(ordered):
        if previous > 0:
            out[day] = close / previous - 1
    return out


def max_drawdown_pct(series: Series) -> float | None:
    """Largest peak-to-trough fall of the close, as a negative percent (e.g. -34.2)."""
    ordered = sorted(series, key=lambda point: point[0])
    if len(ordered) < 2:  # noqa: PLR2004 — a drawdown needs at least a start and an end
        return None
    peak = ordered[0][1]
    worst = 0.0
    for _, close in ordered:
        peak = max(peak, close)
        if peak > 0:
            worst = min(worst, close / peak - 1)
    return round(worst * 100, 2)


def annualised_volatility_pct(series: Series) -> float | None:
    returns = list(_daily_returns(series).values())
    if len(returns) < _MIN_OBSERVATIONS:
        return None
    return round(pstdev(returns) * sqrt(_TRADING_DAYS_PER_YEAR) * 100, 2)


def beta(asset: Series, benchmark: Series) -> float | None:
    """Covariance of the asset's daily returns with the benchmark's over their common dates, divided
    by the benchmark's variance. `None` with too few shared observations or a flat benchmark."""
    asset_returns = _daily_returns(asset)
    bench_returns = _daily_returns(benchmark)
    common = sorted(set(asset_returns) & set(bench_returns))
    if len(common) < _MIN_OBSERVATIONS:
        return None
    a = [asset_returns[day] for day in common]
    b = [bench_returns[day] for day in common]
    mean_a, mean_b = fmean(a), fmean(b)
    variance = sum((x - mean_b) ** 2 for x in b)
    if variance == 0:
        return None
    covariance = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    return round(covariance / variance, 2)


def price_return_pct(series: Series) -> float | None:
    """First close to last close, as a percent."""
    ordered = sorted(series, key=lambda point: point[0])
    if len(ordered) < 2 or ordered[0][1] <= 0:  # noqa: PLR2004
        return None
    return round((ordered[-1][1] / ordered[0][1] - 1) * 100, 2)


def price_cagr_pct(series: Series) -> float | None:
    """Compound annual price growth over the actual span of the series."""
    ordered = sorted(series, key=lambda point: point[0])
    if len(ordered) < 2 or ordered[0][1] <= 0:  # noqa: PLR2004
        return None
    years = (ordered[-1][0] - ordered[0][0]).days / _DAYS_PER_YEAR
    if years < 1:
        return None
    return round(((ordered[-1][1] / ordered[0][1]) ** (1 / years) - 1) * 100, 2)


def indexed_monthly(series: Series, *, base: float = 100.0) -> list[tuple[str, float]]:
    """The series rebased to `base` at its first close and thinned to one point per month (the last
    close of each month) — a chart-sized line, not every trading day."""
    ordered = sorted(series, key=lambda point: point[0])
    if not ordered or ordered[0][1] <= 0:
        return []
    start = ordered[0][1]
    last_per_month: dict[str, float] = {}
    for day, close in ordered:
        last_per_month[f"{day.year}-{day.month:02d}"] = close
    return [(month, round(close / start * base, 2)) for month, close in last_per_month.items()]


__all__ = [
    "adjust_discontinuities",
    "annualised_volatility_pct",
    "beta",
    "indexed_monthly",
    "max_drawdown_pct",
    "price_cagr_pct",
    "price_return_pct",
]
