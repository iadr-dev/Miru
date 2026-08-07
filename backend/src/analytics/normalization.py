"""Cross-sectional normalization primitives for the multi-factor score — winsorize, z-score
against a comparison universe, and map a z-score onto the 0-100 scale the composite blend and
its descriptive bands already speak.

**Governance framing carried over from the score module:** nothing here produces or implies a
trading instruction. These are pure statistical transforms; an input that cannot be normalized
(too small a universe, a metric this symbol genuinely lacks) yields `None` — the caller drops it
and discloses the gap, never a fabricated neutral fill-in.

**Why z-score-then-normal-CDF rather than a second cross-sectional rank pass.** The real
factor-construction convention is winsorize -> z-score standardize (against sector peers where a
classification exists, else the whole universe) -> equal-weight-combine the sub-metric z-scores
within a factor. To hand the existing composite a value on its 0-100 currency (the same scale
momentum/trend/flow already rank on, and the same scale the 40/60 band cut-offs assume), the
combined factor z-score is mapped through the standard normal CDF. That mapping is monotone and
deterministic, needs no second universe traversal, and keeps a factor's contribution on one
comparable "higher is stronger" 0-100 axis alongside the rank-percentile factors.

The winsorization bound (clip at +/- 3 standard deviations) is a stable methodological constant,
not a per-deployment knob — the same bound the momentum/flow percentile path already uses, kept
identical here so one factor is not winsorized differently from another.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

# Same clip the cross-sectional percentile path uses — one winsorization rule across all factors.
_WINSORIZE_SIGMA = 3.0
# Smallest universe over which a z-score (or a percentile) is statistically meaningful.
_MIN_CROSS_SECTION_SIZE = 2
_IDENTICAL_UNIVERSE_ZSCORE = 0.0  # no dispersion -> every member sits exactly at the mean


def winsorize(universe: Sequence[float], *, sigma: float = _WINSORIZE_SIGMA) -> list[float]:
    """Clip each value to `mean +/- sigma * population-stdev` so one fat-tailed outlier cannot
    dominate the standardization. A zero-dispersion universe is returned unchanged (nothing to
    clip)."""
    if not universe:
        return []
    mean = statistics.fmean(universe)
    stdev = statistics.pstdev(universe)
    if stdev == 0:
        return list(universe)
    low, high = mean - sigma * stdev, mean + sigma * stdev
    return [min(max(value, low), high) for value in universe]


def zscore(
    value: float,
    universe: Sequence[float],
    *,
    sigma: float = _WINSORIZE_SIGMA,
    higher_is_better: bool = True,
) -> float | None:
    """Standardize `value` against `universe`, winsorizing the universe (and clipping `value` to
    the same bounds) first.

    `higher_is_better=False` flips the sign so a penalty-direction metric (e.g. debt/equity,
    EV/EBITDA — lower is stronger) contributes positively to a strong factor score, keeping every
    combined sub-metric on one "higher z = stronger" convention.

    `None` when the universe is too small for a meaningful cross-section. An all-identical
    universe has no dispersion, so every member's z-score is exactly 0 (neutral, but genuinely
    available data — not a dropped factor)."""
    if len(universe) < _MIN_CROSS_SECTION_SIZE:
        return None
    clipped = winsorize(universe, sigma=sigma)
    mean = statistics.fmean(clipped)
    stdev = statistics.pstdev(clipped)
    if stdev == 0:
        return _IDENTICAL_UNIVERSE_ZSCORE
    low, high = mean - sigma * stdev, mean + sigma * stdev
    clipped_value = min(max(value, low), high)
    z = (clipped_value - mean) / stdev
    return z if higher_is_better else -z


def combine_zscores(scores: Sequence[float | None]) -> float | None:
    """Equal-weight mean over the *available* sub-metric z-scores (the standard within-factor
    combination). `None` if every sub-metric is unavailable — the factor then degrades as a whole
    rather than being reported off a fabricated value."""
    available = [s for s in scores if s is not None]
    if not available:
        return None
    return statistics.fmean(available)


def zscore_to_percentile(z: float) -> float:
    """Map a z-score onto 0-100 via the standard normal CDF, so a factor built from z-scores lands
    on the same 0-100 "higher is stronger" axis the composite blend and its bands already use."""
    percentile = 100.0 * 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return min(max(percentile, 0.0), 100.0)


__all__ = [
    "combine_zscores",
    "winsorize",
    "zscore",
    "zscore_to_percentile",
]
