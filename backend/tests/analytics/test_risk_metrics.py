"""Risk metrics on hand-checkable series."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from analytics.risk_metrics import (
    adjust_discontinuities,
    annualised_volatility_pct,
    beta,
    indexed_monthly,
    max_drawdown_pct,
    price_cagr_pct,
    price_return_pct,
)


def _series(closes: list[float], start: date = date(2024, 1, 1)) -> list[tuple[date, float]]:
    return [(start + timedelta(days=i), close) for i, close in enumerate(closes)]


def test_max_drawdown_is_the_worst_peak_to_trough_fall() -> None:
    # 100 -> 150 (peak) -> 90 (trough) -> 120: worst fall is 150 -> 90 = -40%.
    assert max_drawdown_pct(_series([100, 150, 90, 120])) == pytest.approx(-40.0)
    assert max_drawdown_pct(_series([100, 110, 120])) == 0.0  # never fell
    assert max_drawdown_pct(_series([100])) is None


def test_beta_of_a_leveraged_copy_is_its_leverage() -> None:
    market = [100.0]
    asset = [100.0]
    for i in range(80):
        step = 0.01 if i % 3 else -0.015
        market.append(market[-1] * (1 + step))
        asset.append(asset[-1] * (1 + 2 * step))  # exactly twice the market's daily move

    assert beta(_series(asset), _series(market)) == pytest.approx(2.0, abs=0.01)


def test_beta_is_none_without_enough_shared_history_or_a_flat_benchmark() -> None:
    assert beta(_series([100, 101, 102]), _series([100, 101, 102])) is None
    flat = _series([100.0] * 60)
    moving = _series([100 + (i % 5) for i in range(60)])
    assert beta(moving, flat) is None


def test_volatility_needs_enough_observations() -> None:
    assert annualised_volatility_pct(_series([100, 101, 99])) is None
    closes = [100 * (1.01 if i % 2 else 0.99) ** 1 for i in range(80)]
    assert annualised_volatility_pct(_series(closes)) is not None


def test_price_return_and_cagr() -> None:
    assert price_return_pct(_series([100, 150])) == pytest.approx(50.0)
    two_years = [(date(2022, 1, 1), 100.0), (date(2024, 1, 1), 144.0)]
    assert price_cagr_pct(two_years) == pytest.approx(20.0, abs=0.1)  # sqrt(1.44) - 1
    assert price_cagr_pct([(date(2024, 1, 1), 100.0), (date(2024, 3, 1), 110.0)]) is None  # <1y


def test_indexed_monthly_rebases_and_keeps_the_last_close_of_each_month() -> None:
    series = [
        (date(2024, 1, 2), 50.0),
        (date(2024, 1, 31), 60.0),
        (date(2024, 2, 29), 75.0),
    ]

    assert indexed_monthly(series) == [("2024-01", 120.0), ("2024-02", 150.0)]
    assert indexed_monthly([]) == []


def test_a_split_is_rebased_so_it_does_not_read_as_a_crash() -> None:
    # 100 -> 104 -> (4-for-1 split) 26 -> 27: unadjusted, that is a -75% day.
    series = _series([100, 104, 26, 27])

    adjusted, breaks = adjust_discontinuities(series, max_abs_move=0.105)

    assert breaks == [series[2][0]]
    assert [round(close, 2) for _, close in adjusted] == [25.0, 26.0, 26.0, 27.0]
    assert max_drawdown_pct(adjusted) == 0.0  # nothing actually fell
    assert max_drawdown_pct(series) == pytest.approx(-75.0)  # what the raw series would claim


def test_moves_inside_the_threshold_are_left_alone() -> None:
    series = _series([100, 109, 98, 105])  # a volatile but legal run of Taiwan-sized moves

    adjusted, breaks = adjust_discontinuities(series, max_abs_move=0.105)

    assert breaks == []
    assert [close for _, close in adjusted] == [100, 109, 98, 105]


def test_a_reverse_split_is_rebased_too() -> None:
    adjusted, breaks = adjust_discontinuities(_series([10, 10.5, 42, 43]), max_abs_move=0.105)

    assert len(breaks) == 1
    assert [round(close, 2) for _, close in adjusted][:2] == [40.0, 42.0]
