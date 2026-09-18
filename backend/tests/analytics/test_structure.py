"""支撐/壓力 swing-pivot detection tests."""

from __future__ import annotations

from analytics import structure
from analytics.indicators import PriceBar


def _bar(high: float, low: float, close: float) -> PriceBar:
    return PriceBar(high=high, low=low, close=close, volume=0)


def test_finds_a_swing_low_support_level() -> None:
    # A clean V over 7 bars: lows 10,9,8,7,8,9,10 — bar 3 (low=7) is a strict local minimum
    # over the centered 3-each-side window.
    bars = [
        _bar(high=low + 1, low=low, close=low + 0.5)
        for low in (10.0, 9.0, 8.0, 7.0, 8.0, 9.0, 10.0)
    ]
    result = structure.support_resistance_levels(bars)
    supports = [lvl for lvl in result.levels if lvl.kind == "support"]
    assert supports == [structure.SupportResistanceLevel(kind="support", price=7.0, bar_index=3)]


def test_finds_a_swing_high_resistance_level() -> None:
    bars = [
        _bar(high=high, low=high - 1, close=high - 0.5)
        for high in (10.0, 11.0, 12.0, 13.0, 12.0, 11.0, 10.0)
    ]
    result = structure.support_resistance_levels(bars)
    resistances = [lvl for lvl in result.levels if lvl.kind == "resistance"]
    assert resistances == [
        structure.SupportResistanceLevel(kind="resistance", price=13.0, bar_index=3)
    ]


def test_too_short_series_yields_no_levels() -> None:
    bars = [_bar(high=10.0, low=9.0, close=9.5) for _ in range(6)]
    assert structure.support_resistance_levels(bars).levels == ()


def test_flat_series_has_no_strict_pivot() -> None:
    bars = [_bar(high=10.0, low=9.0, close=9.5) for _ in range(9)]
    assert structure.support_resistance_levels(bars).levels == ()
