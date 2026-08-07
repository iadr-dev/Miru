"""Normalization-pipeline tests: winsorize clips tails, z-score standardizes (and flips sign for
penalty-direction metrics), the small/degenerate-universe edge cases degrade honestly, and the
z-to-percentile mapping lands on the expected 0-100 anchors."""

from __future__ import annotations

import pytest

from analytics.normalization import (
    combine_zscores,
    winsorize,
    zscore,
    zscore_to_percentile,
)


def test_winsorize_clips_extreme_outlier_to_three_sigma() -> None:
    # A tight cluster plus one far outlier: the cluster keeps the stdev small enough that the
    # +/-3-sigma bound sits well below the outlier, so it is pulled in.
    universe = [1.0] * 20 + [1000.0]
    clipped = winsorize(universe)
    assert max(clipped) < 1000.0  # the outlier was clipped
    assert clipped[:20] == universe[:20]  # in-bound values untouched


def test_winsorize_empty_and_zero_dispersion_are_returned_unchanged() -> None:
    assert winsorize([]) == []
    assert winsorize([5.0, 5.0, 5.0]) == [5.0, 5.0, 5.0]


def test_zscore_single_symbol_universe_is_none() -> None:
    # A universe of one is not a meaningful cross-section — degrade, never fabricate.
    assert zscore(5.0, [5.0]) is None


def test_zscore_all_identical_universe_is_neutral_zero() -> None:
    # No dispersion: every member sits exactly at the mean. Genuinely available (0.0), not dropped.
    assert zscore(5.0, [5.0, 5.0, 5.0, 5.0]) == 0.0


def test_zscore_higher_is_better_positive_for_above_mean() -> None:
    universe = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert zscore(5.0, universe, higher_is_better=True) == pytest.approx(1.414213, abs=1e-4)


def test_zscore_penalty_direction_flips_sign() -> None:
    universe = [1.0, 2.0, 3.0, 4.0, 5.0]
    high = zscore(5.0, universe, higher_is_better=True)
    penalty = zscore(5.0, universe, higher_is_better=False)
    assert high is not None and penalty is not None
    assert penalty == pytest.approx(-high)


def test_combine_zscores_equal_weights_available_only() -> None:
    assert combine_zscores([1.0, None, 3.0]) == pytest.approx(2.0)


def test_combine_zscores_all_none_is_none() -> None:
    assert combine_zscores([None, None]) is None


def test_zscore_to_percentile_anchors() -> None:
    assert zscore_to_percentile(0.0) == pytest.approx(50.0)
    assert zscore_to_percentile(10.0) == pytest.approx(100.0, abs=1e-6)
    assert zscore_to_percentile(-10.0) == pytest.approx(0.0, abs=1e-6)
    # Monotone: a stronger z maps to a higher percentile.
    assert zscore_to_percentile(1.0) > zscore_to_percentile(-1.0)
