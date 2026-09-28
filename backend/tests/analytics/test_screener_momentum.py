"""Unit tests for the 熱門 (momentum) predicates — hand-built volume/close series with known
surges/extremes, plus the insufficient-history graceful-`False` contract."""

from __future__ import annotations

from analytics.indicators import PriceBar
from analytics.screener import momentum


def _bars_with_volume(volumes: list[int]) -> list[PriceBar]:
    return [PriceBar(high=10.0, low=9.0, close=9.5, volume=v) for v in volumes]


def test_volume_surge_fires_at_exactly_the_multiple() -> None:
    # 20 baseline days at volume 100 (avg 100), then today's volume is exactly 2x -> fires.
    volumes = [100] * momentum.VOLUME_SURGE_WINDOW + [200]
    bars = _bars_with_volume(volumes)
    assert momentum.volume_surge(bars) is True


def test_volume_surge_false_below_the_multiple() -> None:
    volumes = [100] * momentum.VOLUME_SURGE_WINDOW + [199]
    bars = _bars_with_volume(volumes)
    assert momentum.volume_surge(bars) is False


def test_volume_surge_baseline_excludes_todays_own_volume() -> None:
    # If today's huge volume were folded into its own baseline, the ratio would shrink below the
    # threshold. The baseline must be strictly the 20 days BEFORE today.
    volumes = [10] * momentum.VOLUME_SURGE_WINDOW + [1000]
    bars = _bars_with_volume(volumes)
    assert momentum.volume_surge(bars) is True


def test_volume_surge_false_with_insufficient_history() -> None:
    volumes = [100] * (momentum.VOLUME_SURGE_WINDOW - 1) + [1000]
    bars = _bars_with_volume(volumes)
    assert momentum.volume_surge(bars) is False


def test_volume_surge_false_with_zero_baseline() -> None:
    volumes = [0] * momentum.VOLUME_SURGE_WINDOW + [100]
    bars = _bars_with_volume(volumes)
    assert momentum.volume_surge(bars) is False


def test_volume_contraction_fires_at_exactly_the_fraction() -> None:
    # 20 baseline days at volume 100 (avg 100), then today's volume is exactly half -> fires.
    volumes = [100] * momentum.VOLUME_SURGE_WINDOW + [50]
    bars = _bars_with_volume(volumes)
    assert momentum.volume_contraction(bars) is True


def test_volume_contraction_false_above_the_fraction() -> None:
    volumes = [100] * momentum.VOLUME_SURGE_WINDOW + [51]
    bars = _bars_with_volume(volumes)
    assert momentum.volume_contraction(bars) is False


def test_volume_contraction_baseline_excludes_todays_own_volume() -> None:
    # If today's near-zero volume were folded into its own baseline, the ratio would rise above
    # the threshold. The baseline must be strictly the 20 days BEFORE today.
    volumes = [1000] * momentum.VOLUME_SURGE_WINDOW + [1]
    bars = _bars_with_volume(volumes)
    assert momentum.volume_contraction(bars) is True


def test_volume_contraction_false_with_insufficient_history() -> None:
    volumes = [100] * (momentum.VOLUME_SURGE_WINDOW - 1) + [1]
    bars = _bars_with_volume(volumes)
    assert momentum.volume_contraction(bars) is False


def test_volume_contraction_false_with_zero_baseline() -> None:
    volumes = [0] * momentum.VOLUME_SURGE_WINDOW + [0]
    bars = _bars_with_volume(volumes)
    assert momentum.volume_contraction(bars) is False


def _bars_with_closes(closes: list[float]) -> list[PriceBar]:
    return [PriceBar(high=c, low=c, close=c, volume=0) for c in closes]


def test_new_52w_high_fires_on_a_fresh_max() -> None:
    closes = [float(50 + i) for i in range(momentum.FIFTY_TWO_WEEK_WINDOW - 1)] + [500.0]
    bars = _bars_with_closes(closes)
    assert momentum.new_52w_high(bars) is True
    assert momentum.new_52w_low(bars) is False


def test_new_52w_high_false_when_not_the_max() -> None:
    closes = [float(50 + i) for i in range(momentum.FIFTY_TWO_WEEK_WINDOW - 1)] + [1.0]
    bars = _bars_with_closes(closes)
    assert momentum.new_52w_high(bars) is False


def test_new_52w_low_fires_on_a_fresh_min() -> None:
    closes = [float(500 - i) for i in range(momentum.FIFTY_TWO_WEEK_WINDOW - 1)] + [0.5]
    bars = _bars_with_closes(closes)
    assert momentum.new_52w_low(bars) is True
    assert momentum.new_52w_high(bars) is False


def test_new_52w_extremes_false_with_insufficient_history() -> None:
    closes = [float(50 + i) for i in range(momentum.FIFTY_TWO_WEEK_WINDOW - 1)]
    bars = _bars_with_closes(closes)
    assert momentum.new_52w_high(bars) is False
    assert momentum.new_52w_low(bars) is False
