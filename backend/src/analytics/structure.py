"""Chart structure signals — 支撐/壓力 (support/resistance) swing-pivot detection over an OHLC
series. Deferred out of `analytics/indicators.py` per that module's own docstring: this builds
on the base indicators, is itself a distinct feature, and only needed once the K線 chart's
structure-marker overlay shipped.

Pure and deterministic, same contract as `indicators.py`/`patterns.py`: no I/O, bars supplied
in ascending `trade_date` order, caller's responsibility to sort.
"""

from __future__ import annotations

from collections.abc import Sequence

from analytics.indicators import PriceBar
from analytics.models import SupportResistanceLevel, SupportResistanceResult

# A swing high/low is judged against `_PIVOT_SPAN` bars on each side — the pivot bar's high (low)
# must be the strict max (min) over the `2*_PIVOT_SPAN + 1`-bar window centered on it. 3 is a
# standard swing-pivot span: short enough to catch swings on a several-month daily window, long
# enough that every single-bar wiggle doesn't register as a "level".
_PIVOT_SPAN = 3


def support_resistance_levels(
    bars: Sequence[PriceBar], span: int = _PIVOT_SPAN
) -> SupportResistanceResult:
    """A swing-low pivot (strict local minimum of `low` over the centered window) becomes a
    support level at that bar's `low`; a swing-high pivot (strict local maximum of `high`)
    becomes a resistance level at that bar's `high`. Returns every such pivot found, oldest
    first — never a fabricated "the" support/resistance level when the window is too short to
    have one.

    `span` is the number of bars judged on each side of a pivot (default `_PIVOT_SPAN`, which the
    chart's 支撐/壓力 lines use). `analytics/chart_patterns.py` passes a wider span so its multi-bar
    shapes are built on swing-scale pivots - the same definition, a different scale, not a second
    pivot rule."""
    n = len(bars)
    levels: list[SupportResistanceLevel] = []
    if span < 1 or n < 2 * span + 1:
        return SupportResistanceResult(levels=())

    for i in range(span, n - span):
        window = bars[i - span : i + span + 1]
        pivot = bars[i]
        others_low = [b.low for j, b in enumerate(window) if j != span]
        others_high = [b.high for j, b in enumerate(window) if j != span]
        if all(pivot.low < low for low in others_low):
            levels.append(SupportResistanceLevel(kind="support", price=pivot.low, bar_index=i))
        if all(pivot.high > high for high in others_high):
            levels.append(SupportResistanceLevel(kind="resistance", price=pivot.high, bar_index=i))

    return SupportResistanceResult(levels=tuple(levels))


__all__ = ["support_resistance_levels"]
