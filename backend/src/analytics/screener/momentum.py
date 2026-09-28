"""Volume-surge and price-extreme predicates over an end-of-day bar series (熱門/momentum tags).

These answer a different question from `events.py`'s indicator crosses: not "did an indicator
cross today" but "is today's volume or price an outlier against its own trailing history". Same
shape as `events.py` otherwise — pure, deterministic, no I/O, ascending-by-date `PriceBar` input,
`False` (never an error) when there isn't enough history to judge the last bar. Kept in a separate
module rather than folded into `events.py` because these predicates key off `volume`/`turnover`
and a trailing extreme rather than an indicator series, a distinct enough concern to warrant its
own file, matching the existing per-concern split (`events.py` = technical, `chip_streaks.py` =
chip, `patterns.py` = candlestick).
"""

from __future__ import annotations

from collections.abc import Sequence

from analytics.indicators import PriceBar

# "爆量" (volume-surge) as TW retail screeners commonly define it: today's volume at least this
# many times its own trailing average. 2x is the conventional threshold — a day that visibly
# stands out from recent activity without being so extreme (5x+) that it only catches rare spikes.
VOLUME_SURGE_MULTIPLE = 2.0

# The trailing average window the surge multiple is measured against. 20 trading days (~1 month)
# is the standard "recent normal" baseline TW volume-surge screens use — long enough to smooth out
# a single prior spike, short enough to track a genuine regime change in turnover.
VOLUME_SURGE_WINDOW = 20

# "量縮" (volume-contraction) — the mirror-image screen: today's volume at most this fraction of
# its own trailing average. 0.5 (half of normal) is the conventional threshold, symmetric with
# `VOLUME_SURGE_MULTIPLE`'s 2x on the other side.
VOLUME_CONTRACTION_FRACTION = 0.5

# The 52-week-high/low lookback. Matches `analytics/score.py`'s `_TREND_TEMPLATE_LOOKBACK` VALUE
# (252 trading days ~ 52 weeks) for consistency with the rest of the codebase's "52-week" meaning,
# without importing from `score.py` — this package stays decoupled from the composite-score module
# (see `batch.py`'s layer note for why the two are never wired together).
FIFTY_TWO_WEEK_WINDOW = 252


def volume_surge(bars: Sequence[PriceBar], *, multiple: float = VOLUME_SURGE_MULTIPLE) -> bool:
    """Today's volume is at least `multiple` times the average volume of the `VOLUME_SURGE_WINDOW`
    trading days strictly before today (the trailing average excludes today itself, so a surge is
    always measured against a truly independent baseline, never partly against itself). `False`
    if fewer than `VOLUME_SURGE_WINDOW + 1` bars are available, or the trailing average is zero
    (a run of no-volume days has no meaningful baseline to compare against)."""
    if len(bars) < VOLUME_SURGE_WINDOW + 1:
        return False
    baseline_bars = bars[-(VOLUME_SURGE_WINDOW + 1) : -1]
    baseline_avg = sum(bar.volume for bar in baseline_bars) / VOLUME_SURGE_WINDOW
    if baseline_avg <= 0:
        return False
    return bars[-1].volume >= multiple * baseline_avg


def volume_contraction(
    bars: Sequence[PriceBar], *, fraction: float = VOLUME_CONTRACTION_FRACTION
) -> bool:
    """Today's volume is at most `fraction` times the average volume of the `VOLUME_SURGE_WINDOW`
    trading days strictly before today — the mirror-image of `volume_surge`, same trailing-average
    baseline (excludes today itself) and the same `False`-when-insufficient-history/zero-baseline
    rules."""
    if len(bars) < VOLUME_SURGE_WINDOW + 1:
        return False
    baseline_bars = bars[-(VOLUME_SURGE_WINDOW + 1) : -1]
    baseline_avg = sum(bar.volume for bar in baseline_bars) / VOLUME_SURGE_WINDOW
    if baseline_avg <= 0:
        return False
    return bars[-1].volume <= fraction * baseline_avg


def new_52w_high(bars: Sequence[PriceBar]) -> bool:
    """Today's close is the highest close over the trailing `FIFTY_TWO_WEEK_WINDOW` trading days
    (today included). `False` if fewer than `FIFTY_TWO_WEEK_WINDOW` bars are available — a partial
    window is not a proven 52-week extreme."""
    if len(bars) < FIFTY_TWO_WEEK_WINDOW:
        return False
    window = bars[-FIFTY_TWO_WEEK_WINDOW:]
    return bars[-1].close >= max(bar.close for bar in window)


def new_52w_low(bars: Sequence[PriceBar]) -> bool:
    """Today's close is the lowest close over the trailing `FIFTY_TWO_WEEK_WINDOW` trading days
    (today included). `False` if fewer than `FIFTY_TWO_WEEK_WINDOW` bars are available."""
    if len(bars) < FIFTY_TWO_WEEK_WINDOW:
        return False
    window = bars[-FIFTY_TWO_WEEK_WINDOW:]
    return bars[-1].close <= min(bar.close for bar in window)


__all__ = [
    "FIFTY_TWO_WEEK_WINDOW",
    "VOLUME_CONTRACTION_FRACTION",
    "VOLUME_SURGE_MULTIPLE",
    "VOLUME_SURGE_WINDOW",
    "new_52w_high",
    "new_52w_low",
    "volume_contraction",
    "volume_surge",
]
