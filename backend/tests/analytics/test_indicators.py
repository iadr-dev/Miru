"""Indicator library tests — each checked against a hand-computed reference value over a small
fixture series (pure arithmetic; no I/O, no fixtures needed)."""

from __future__ import annotations

import math

from analytics.indicators import (
    PriceBar,
    atr,
    bars_from_close,
    bollinger,
    ema,
    kd,
    macd,
    obv,
    rsi,
    sma,
    vwap,
)


def test_sma_warms_up_then_matches_hand_computation() -> None:
    bars = bars_from_close([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    result = sma(bars, window=3)

    assert result.values[0] is None
    assert result.values[1] is None
    assert result.values[2] == 2.0  # mean(1,2,3)
    assert result.values[3] == 3.0  # mean(2,3,4)
    assert result.values[-1] == 9.0  # mean(8,9,10)


def test_ema_seeds_with_sma_then_smooths() -> None:
    bars = bars_from_close([1.0, 2.0, 3.0, 4.0, 5.0])
    result = ema(bars, window=3)

    assert result.values[0] is None
    assert result.values[1] is None
    assert result.values[2] == 2.0  # seed = mean(1,2,3)
    assert result.values[3] == 3.0  # 0.5*4 + 0.5*2
    assert result.values[4] == 4.0  # 0.5*5 + 0.5*3


def test_rsi_matches_hand_computed_reference() -> None:
    # diffs: +1,-1,+1,+1 -> gains=[1,0,1,1], losses=[0,1,0,0]
    bars = bars_from_close([1.0, 2.0, 1.0, 2.0, 3.0])
    result = rsi(bars, period=2)

    assert result.values[0] is None
    assert result.values[1] is None
    assert result.values[2] == 50.0
    assert result.values[3] == 75.0
    assert result.values[4] == 87.5


def test_rsi_all_gains_saturates_at_100() -> None:
    bars = bars_from_close([1.0, 2.0, 3.0, 4.0])
    result = rsi(bars, period=2)

    assert result.values[2] == 100.0
    assert result.values[3] == 100.0


def test_macd_matches_hand_computed_ema_relationship() -> None:
    bars = bars_from_close([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    result = macd(bars, fast=2, slow=3, signal=2)

    assert result.dif[2] == 0.5
    assert result.dif[5] == 0.5
    assert result.dem[2] is None  # signal line not yet seeded
    assert result.dem[3] == 0.5
    assert result.histogram[3] == 0.0
    assert result.histogram[2] is None


def test_kd_warms_up_then_matches_hand_computation() -> None:
    # period=3, seeded K=D=50. Hand-computed over an explicit high/low/close series:
    #   idx2 window (bars 0-2): HH=14, LL=8, span=6, close=13 -> RSV=250/3
    #        K = 50*2/3 + (250/3)/3 = 550/9;  D = 50*2/3 + (550/9)/3 = 1450/27
    #   idx3 window (bars 1-3): HH=14, LL=9, span=5, close=12 -> RSV=60
    #        K = (550/9)*2/3 + 60/3 = 1640/27;  D = (1450/27)*2/3 + (1640/27)/3 = 4540/81
    bars = [
        PriceBar(high=10.0, low=8.0, close=9.0, volume=0),
        PriceBar(high=12.0, low=9.0, close=11.0, volume=0),
        PriceBar(high=14.0, low=10.0, close=13.0, volume=0),
        PriceBar(high=13.0, low=11.0, close=12.0, volume=0),
    ]
    result = kd(bars, period=3, k_smoothing=3, d_smoothing=3)

    assert result.k[0] is None
    assert result.k[1] is None
    assert result.d[0] is None
    assert result.d[1] is None
    assert result.k[2] is not None and math.isclose(result.k[2], 550 / 9, rel_tol=1e-12)
    assert result.d[2] is not None and math.isclose(result.d[2], 1450 / 27, rel_tol=1e-12)
    assert result.k[3] is not None and math.isclose(result.k[3], 1640 / 27, rel_tol=1e-12)
    assert result.d[3] is not None and math.isclose(result.d[3], 4540 / 81, rel_tol=1e-12)


def test_kd_flat_window_takes_neutral_fifty() -> None:
    # A perfectly flat window has no range to derive a directional RSV from; the neutral 50 seed
    # holds rather than fabricating a 0/100. period-1 leading bars stay None.
    bars = bars_from_close([5.0, 5.0, 5.0, 5.0])
    result = kd(bars, period=3)
    assert result.k[1] is None
    assert result.k[2] == 50.0
    assert result.d[2] == 50.0
    assert result.k[3] == 50.0


def test_kd_too_short_is_all_none() -> None:
    result = kd(bars_from_close([1.0, 2.0]), period=9)
    assert all(v is None for v in result.k)
    assert all(v is None for v in result.d)


def test_bollinger_matches_hand_computed_population_stddev() -> None:
    bars = bars_from_close([1.0, 2.0, 3.0])
    result = bollinger(bars, window=3, num_std=2.0)

    expected_std = math.sqrt(2.0 / 3.0)
    assert result.mid[2] == 2.0
    assert result.upper[2] is not None
    assert result.lower[2] is not None
    assert math.isclose(result.upper[2], 2.0 + 2.0 * expected_std, rel_tol=1e-9)
    assert math.isclose(result.lower[2], 2.0 - 2.0 * expected_std, rel_tol=1e-9)


def test_atr_matches_hand_computed_true_range() -> None:
    bars = [
        PriceBar(high=10.0, low=8.0, close=9.0, volume=0),
        PriceBar(high=11.0, low=9.0, close=10.0, volume=0),
        PriceBar(high=12.0, low=10.0, close=11.0, volume=0),
    ]
    result = atr(bars, period=2)

    assert result.values[0] is None
    assert result.values[1] == 2.0
    assert result.values[2] == 2.0


def test_obv_signs_by_close_direction() -> None:
    bars = [
        PriceBar(high=0, low=0, close=10.0, volume=100),
        PriceBar(high=0, low=0, close=11.0, volume=100),
        PriceBar(high=0, low=0, close=9.0, volume=100),
        PriceBar(high=0, low=0, close=9.0, volume=100),
        PriceBar(high=0, low=0, close=12.0, volume=100),
    ]
    result = obv(bars)

    assert result.values == (0.0, 100.0, 0.0, 0.0, 100.0)


def test_vwap_uses_turnover_over_volume_when_present() -> None:
    bars = [
        PriceBar(high=10.0, low=8.0, close=9.0, volume=100, turnover=900.0),
        PriceBar(high=10.0, low=8.0, close=9.0, volume=0, turnover=None),
        PriceBar(high=10.0, low=8.0, close=9.0, volume=100, turnover=None),
    ]
    result = vwap(bars)

    assert result.values[0] == 9.0  # 900/100
    assert result.values[1] is None  # no trades that day
    assert result.values[2] == 9.0  # (10+8+9)/3 fallback
