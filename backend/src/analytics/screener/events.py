"""Indicator-event detection over an end-of-day bar series.

The `analytics/indicators.py` library computes indicator *series* (a moving-average line, a MACD
histogram); it deliberately stops short of "did an event happen on the last bar" — that gap is
this module. Every function here takes an ascending-by-date `PriceBar` sequence, computes the
indicator it needs through the shared library, and answers a single yes/no about the most recent
bar relative to the one before it (a cross, a slope flip, a breakout).

Pure and deterministic: no I/O, no store, no network. Insufficient history is never an error —
a series too short to define the indicator at the last two bars returns `False`, matching the
`None`-padding-on-warmup convention the indicator library itself uses. Bars must be supplied in
ascending `trade_date` order; like the indicator library, this module never re-sorts.
"""

from __future__ import annotations

from collections.abc import Sequence

from analytics.indicators import PriceBar, kd, macd, sma

# A cross/breakout compares the last bar to the one before it; a slope-flip needs three points
# (two consecutive slopes). Named so the guards below read as "enough points to answer", not a
# bare magic number.
_MIN_FOR_CROSS = 2
_MIN_FOR_SLOPE = 3


def _last_two_defined(values: Sequence[float | None]) -> tuple[float, float] | None:
    """The `(prev, current)` pair at the final two indices, or `None` if either is inside the
    indicator's warm-up window (still `None`) or the series is shorter than two bars."""
    if len(values) < _MIN_FOR_CROSS:
        return None
    prev, current = values[-2], values[-1]
    if prev is None or current is None:
        return None
    return prev, current


def ma_crossed_up(bars: Sequence[PriceBar], fast_window: int, slow_window: int) -> bool:
    """The fast MA crossed above the slow MA today: yesterday fast <= slow, today fast > slow
    (a golden cross). `False` if either MA is undefined at the last two bars."""
    fast = sma(bars, fast_window).values
    slow = sma(bars, slow_window).values
    if len(fast) < _MIN_FOR_CROSS or len(slow) < _MIN_FOR_CROSS:
        return False
    f_prev, f_now = fast[-2], fast[-1]
    s_prev, s_now = slow[-2], slow[-1]
    if f_prev is None or f_now is None or s_prev is None or s_now is None:
        return False
    return f_prev <= s_prev and f_now > s_now


def ma_crossed_down(bars: Sequence[PriceBar], fast_window: int, slow_window: int) -> bool:
    """The fast MA crossed below the slow MA today (a death cross)."""
    fast = sma(bars, fast_window).values
    slow = sma(bars, slow_window).values
    if len(fast) < _MIN_FOR_CROSS or len(slow) < _MIN_FOR_CROSS:
        return False
    f_prev, f_now = fast[-2], fast[-1]
    s_prev, s_now = slow[-2], slow[-1]
    if f_prev is None or f_now is None or s_prev is None or s_now is None:
        return False
    return f_prev >= s_prev and f_now < s_now


def ma_turned_up(bars: Sequence[PriceBar], window: int) -> bool:
    """The MA's own slope flipped from non-rising to rising today: the change over the last step
    is positive while the change over the prior step was not. Needs the MA defined at the final
    three bars."""
    values = sma(bars, window).values
    if len(values) < _MIN_FOR_SLOPE:
        return False
    a, b, c = values[-3], values[-2], values[-1]
    if a is None or b is None or c is None:
        return False
    slope_prev = b - a
    slope_now = c - b
    return slope_prev <= 0 and slope_now > 0


def ma_turned_down(bars: Sequence[PriceBar], window: int) -> bool:
    """The MA's slope flipped from non-falling to falling today."""
    values = sma(bars, window).values
    if len(values) < _MIN_FOR_SLOPE:
        return False
    a, b, c = values[-3], values[-2], values[-1]
    if a is None or b is None or c is None:
        return False
    slope_prev = b - a
    slope_now = c - b
    return slope_prev >= 0 and slope_now < 0


def macd_histogram_crossed_positive(bars: Sequence[PriceBar]) -> bool:
    """The MACD histogram crossed from non-positive to positive today (柱翻紅) — the DIF/DEM
    momentum spread turning up through zero. `False` until the histogram is defined at the last
    two bars."""
    pair = _last_two_defined(macd(bars).histogram)
    if pair is None:
        return False
    prev, current = pair
    return prev <= 0 and current > 0


def macd_histogram_crossed_negative(bars: Sequence[PriceBar]) -> bool:
    """The MACD histogram crossed from non-negative to negative today (柱翻綠)."""
    pair = _last_two_defined(macd(bars).histogram)
    if pair is None:
        return False
    prev, current = pair
    return prev >= 0 and current < 0


def kd_golden_cross(bars: Sequence[PriceBar]) -> bool:
    """%K crossed above %D today (日KD向上交叉): yesterday K <= D, today K > D. Uses the standard
    9/3/3 stochastic. `False` if K or D is undefined at the last two bars (still in warm-up) or
    fewer than two bars exist."""
    result = kd(bars)
    if len(result.k) < _MIN_FOR_CROSS or len(result.d) < _MIN_FOR_CROSS:
        return False
    k_prev, k_now = result.k[-2], result.k[-1]
    d_prev, d_now = result.d[-2], result.d[-1]
    if k_prev is None or k_now is None or d_prev is None or d_now is None:
        return False
    return k_prev <= d_prev and k_now > d_now


def kd_death_cross(bars: Sequence[PriceBar]) -> bool:
    """%K crossed below %D today (日KD向下交叉): yesterday K >= D, today K < D."""
    result = kd(bars)
    if len(result.k) < _MIN_FOR_CROSS or len(result.d) < _MIN_FOR_CROSS:
        return False
    k_prev, k_now = result.k[-2], result.k[-1]
    d_prev, d_now = result.d[-2], result.d[-1]
    if k_prev is None or k_now is None or d_prev is None or d_now is None:
        return False
    return k_prev >= d_prev and k_now < d_now


def breakout_above_ma(bars: Sequence[PriceBar], window: int) -> bool:
    """Close crossed above its MA today: yesterday's close was at or below the MA, today's is
    above it (站上均線). Uses the same-bar MA value against that bar's close, so it captures the
    day the price reclaimed the line. `False` if the MA is undefined at the last two bars or
    fewer than two bars exist."""
    values = sma(bars, window).values
    if len(bars) < _MIN_FOR_CROSS or len(values) < _MIN_FOR_CROSS:
        return False
    ma_prev, ma_now = values[-2], values[-1]
    if ma_prev is None or ma_now is None:
        return False
    close_prev = bars[-2].close
    close_now = bars[-1].close
    return close_prev <= ma_prev and close_now > ma_now


__all__ = [
    "breakout_above_ma",
    "kd_death_cross",
    "kd_golden_cross",
    "ma_crossed_down",
    "ma_crossed_up",
    "ma_turned_down",
    "ma_turned_up",
    "macd_histogram_crossed_negative",
    "macd_histogram_crossed_positive",
]
