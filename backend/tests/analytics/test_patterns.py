"""Candlestick pattern tests — each checked against a short, explicit `PriceBar` fixture whose
expected boolean is verifiable by eye, plus the graceful-`False` contract on short/open-less input.
"""

from __future__ import annotations

from analytics import patterns
from analytics.indicators import PriceBar


def _bar(open_: float, high: float, low: float, close: float) -> PriceBar:
    return PriceBar(high=high, low=low, close=close, volume=0, open=open_)


def test_doji_small_body_relative_to_range() -> None:
    # Body |100.0 - 100.05| = 0.05 vs range 2.0 -> 2.5% << 10% cutoff: a doji.
    assert patterns.is_doji([_bar(100.0, 101.0, 99.0, 100.05)]) is True
    # A full directional body (open 100, close 102) over the same range is not a doji.
    assert patterns.is_doji([_bar(100.0, 102.0, 99.0, 102.0)]) is False
    # A zero-range bar has no range to judge the body against — not asserted.
    assert patterns.is_doji([_bar(50.0, 50.0, 50.0, 50.0)]) is False


def test_bullish_engulfing() -> None:
    # Yesterday red (open 105 -> close 100). Today green opening 99 (<= 100) and closing 106
    # (>= 105): today's body fully engulfs yesterday's.
    bars = [_bar(105.0, 106.0, 99.0, 100.0), _bar(99.0, 107.0, 98.0, 106.0)]
    assert patterns.is_bullish_engulfing(bars) is True
    assert patterns.is_bearish_engulfing(bars) is False


def test_bearish_engulfing() -> None:
    # Yesterday green (100 -> 105). Today red opening 106 (>= 105) and closing 99 (<= 100).
    bars = [_bar(100.0, 106.0, 99.0, 105.0), _bar(106.0, 107.0, 98.0, 99.0)]
    assert patterns.is_bearish_engulfing(bars) is True
    assert patterns.is_bullish_engulfing(bars) is False


def test_bullish_cannon() -> None:
    # Two strong up candles (bodies 5 and 6) bracket a small middle body (0.5, well under
    # 0.5*avg(5,6)=2.75), and the third closes above the first (111 > 105).
    bars = [
        _bar(100.0, 106.0, 99.0, 105.0),
        _bar(105.0, 106.0, 104.0, 105.5),
        _bar(105.0, 112.0, 104.0, 111.0),
    ]
    assert patterns.is_bullish_cannon(bars) is True
    assert patterns.is_bearish_cannon(bars) is False


def test_bearish_cannon() -> None:
    bars = [
        _bar(105.0, 106.0, 99.0, 100.0),
        _bar(100.0, 101.0, 99.0, 99.5),
        _bar(100.0, 101.0, 93.0, 94.0),
    ]
    assert patterns.is_bearish_cannon(bars) is True
    assert patterns.is_bullish_cannon(bars) is False


def test_cannon_rejects_large_middle_body() -> None:
    # Middle body 4.0 exceeds 0.5*avg(5,6)=2.75 -> not a small rest bar, so not a cannon.
    bars = [
        _bar(100.0, 106.0, 99.0, 105.0),
        _bar(105.0, 110.0, 104.0, 109.0),
        _bar(109.0, 112.0, 108.0, 111.0),
    ]
    assert patterns.is_bullish_cannon(bars) is False


def test_v_reversal() -> None:
    # Lows 5,4,3,4,5: strictly declining into the pivot at index 2, strictly rising out.
    bars = [
        _bar(6.0, 7.0, 5.0, 6.0),
        _bar(5.0, 6.0, 4.0, 5.0),
        _bar(4.0, 5.0, 3.0, 4.0),
        _bar(5.0, 6.0, 4.0, 5.0),
        _bar(6.0, 7.0, 5.0, 6.0),
    ]
    assert patterns.is_v_reversal(bars) is True
    assert patterns.is_inverse_v_reversal(bars) is False


def test_inverse_v_reversal() -> None:
    # Highs 5,6,7,6,5: strictly rising into the pivot at index 2, strictly declining out.
    bars = [
        _bar(4.0, 5.0, 3.0, 4.0),
        _bar(5.0, 6.0, 4.0, 5.0),
        _bar(6.0, 7.0, 5.0, 6.0),
        _bar(5.0, 6.0, 4.0, 5.0),
        _bar(4.0, 5.0, 3.0, 4.0),
    ]
    assert patterns.is_inverse_v_reversal(bars) is True
    assert patterns.is_v_reversal(bars) is False


def test_v_reversal_rejects_edge_pivot() -> None:
    # Lowest low sits at the last bar (no rising leg out) — not a completed V.
    bars = [
        _bar(9.0, 10.0, 8.0, 9.0),
        _bar(8.0, 9.0, 7.0, 8.0),
        _bar(7.0, 8.0, 6.0, 7.0),
        _bar(6.0, 7.0, 5.0, 6.0),
        _bar(5.0, 6.0, 4.0, 5.0),
    ]
    assert patterns.is_v_reversal(bars) is False


def test_three_rising_and_falling() -> None:
    rising = [
        _bar(10.0, 12.0, 9.0, 11.0),
        _bar(11.0, 13.0, 10.0, 12.5),
        _bar(12.5, 15.0, 12.0, 14.0),
    ]
    assert patterns.is_three_rising(rising) is True
    assert patterns.is_three_falling(rising) is False

    falling = [
        _bar(14.0, 15.0, 12.0, 12.5),
        _bar(12.5, 13.0, 10.0, 11.0),
        _bar(11.0, 12.0, 8.0, 9.0),
    ]
    assert patterns.is_three_falling(falling) is True
    assert patterns.is_three_rising(falling) is False


def test_three_rising_rejects_a_doji_member() -> None:
    # The middle bar is a doji (open ≈ close) — not three genuine bodies.
    bars = [
        _bar(10.0, 12.0, 9.0, 11.0),
        _bar(11.5, 13.0, 11.0, 11.55),
        _bar(11.55, 15.0, 11.0, 14.0),
    ]
    assert patterns.is_three_rising(bars) is False


def test_short_or_openless_input_is_false() -> None:
    assert patterns.is_bullish_engulfing([_bar(1.0, 2.0, 0.5, 1.5)]) is False
    assert patterns.is_bullish_cannon([]) is False
    assert patterns.is_v_reversal([_bar(1.0, 2.0, 0.5, 1.5)]) is False
    # An open-less bar (only HLC known) can't have its body judged.
    openless = PriceBar(high=2.0, low=1.0, close=1.5, volume=0)
    assert patterns.is_doji([openless]) is False
    assert patterns.is_three_rising([openless, openless, openless]) is False
