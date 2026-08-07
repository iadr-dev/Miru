"""Unit tests for the indicator-event detection layer — hand-built close series with known
crosses/slope-flips/breakouts, plus the insufficient-history graceful-`False` contract."""

from __future__ import annotations

from analytics.indicators import bars_from_close
from analytics.screener import events


def test_ma_turned_up_detects_slope_flip() -> None:
    # The 5-day MA is still falling into the second-to-last bar, then the final jump flips its
    # slope positive (slope_prev <= 0, slope_now > 0).
    closes = [20, 20, 20, 20, 20, 10, 10, 10, 10, 10, 40]
    bars = bars_from_close([float(c) for c in closes])
    assert events.ma_turned_up(bars, 5) is True
    assert events.ma_turned_down(bars, 5) is False


def test_ma_turned_down_detects_slope_flip() -> None:
    closes = [10, 10, 10, 10, 10, 20, 20, 20, 20, 20, 2]
    bars = bars_from_close([float(c) for c in closes])
    assert events.ma_turned_down(bars, 5) is True
    assert events.ma_turned_up(bars, 5) is False


def test_golden_and_death_cross() -> None:
    # A long decline (fast MA below slow) then a sharp recovery that pulls the 5-day up through
    # the 20-day on the final bar.
    closes = [float(50 - i) for i in range(25)] + [30.0, 45.0, 70.0]
    bars = bars_from_close(closes)
    assert events.ma_crossed_up(bars, 5, 20) is True
    assert events.ma_crossed_down(bars, 5, 20) is False


def test_breakout_above_ma20() -> None:
    # Flat below the 20-day, then a jump above it on the last bar.
    closes = [float(100) for _ in range(20)] + [99.0, 130.0]
    bars = bars_from_close(closes)
    assert events.breakout_above_ma(bars, 20) is True


def test_macd_histogram_cross_positive() -> None:
    # A long downtrend flips to a strong uptrend; the MACD histogram crosses up through zero near
    # the end. Search the series for a bar where the cross fires rather than asserting a fixed
    # index, since the exact crossover bar depends on the EMA seeding.
    down = [float(120 - i) for i in range(40)]
    up = [float(80 + i * 2) for i in range(20)]
    closes = down + up
    fired = any(
        events.macd_histogram_crossed_positive(bars_from_close(closes[: n + 1]))
        for n in range(len(closes))
    )
    assert fired is True


def test_kd_golden_cross_fires_on_upturn() -> None:
    # A long slide (K pinned below D) then a sharp rally that pulls %K up through %D on the final
    # bar. Search for the bar where the up-cross fires rather than fixing an index, since the exact
    # crossover bar depends on the recursive smoothing seed.
    down = [float(60 - i) for i in range(20)]
    up = [float(40 + i * 3) for i in range(8)]
    closes = down + up
    fired = any(
        events.kd_golden_cross(bars_from_close(closes[: n + 1])) for n in range(len(closes))
    )
    assert fired is True


def test_kd_death_cross_fires_on_downturn() -> None:
    up = [float(40 + i) for i in range(20)]
    down = [float(60 - i * 3) for i in range(8)]
    closes = up + down
    fired = any(events.kd_death_cross(bars_from_close(closes[: n + 1])) for n in range(len(closes)))
    assert fired is True


def test_insufficient_history_never_raises_and_is_false() -> None:
    short = bars_from_close([1.0, 2.0])
    empty = bars_from_close([])
    assert events.ma_turned_up(short, 5) is False
    assert events.ma_crossed_up(short, 5, 20) is False
    assert events.breakout_above_ma(short, 20) is False
    assert events.macd_histogram_crossed_positive(short) is False
    assert events.macd_histogram_crossed_negative(empty) is False
    assert events.kd_golden_cross(short) is False
    assert events.kd_death_cross(empty) is False
