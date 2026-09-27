"""Unit tests for the chip-streak predicates — proving the streak vs cumulative-window
semantics are genuinely different and that a short series is never asserted as a proven streak."""

from __future__ import annotations

from analytics.screener import chip_streaks


def test_net_buy_streak_strict_consecutive() -> None:
    assert chip_streaks.net_buy_streak([100.0, 50.0, 200.0], 3) is True
    # A single down day inside the last 3 breaks the streak, even though the window nets positive.
    assert chip_streaks.net_buy_streak([100.0, -10.0, 200.0], 3) is False
    # Fewer than min_days available: unproven, never asserted.
    assert chip_streaks.net_buy_streak([100.0, 50.0], 3) is False


def test_net_sell_streak_strict_consecutive() -> None:
    assert chip_streaks.net_sell_streak([-100.0, -50.0, -200.0], 3) is True
    assert chip_streaks.net_sell_streak([-100.0, 10.0, -200.0], 3) is False


def test_cumulative_net_buy_tolerates_a_down_day() -> None:
    # The window nets positive despite a down day — the looser semantics, distinct from the streak.
    series = [300.0, -50.0, 20.0, -10.0, 100.0]
    assert chip_streaks.cumulative_net_buy(series, 5) is True
    assert chip_streaks.net_buy_streak(series, 5) is False


def test_cumulative_net_buy_negative_window_is_false() -> None:
    assert chip_streaks.cumulative_net_buy([-300.0, 20.0, -10.0, 5.0, 1.0], 5) is False


def test_partial_window_is_not_asserted() -> None:
    assert chip_streaks.cumulative_net_buy([100.0, 100.0], 5) is False


def test_streak_length_positive_for_buy_streak() -> None:
    assert chip_streaks.streak_length([100.0, 50.0, 200.0]) == 3


def test_streak_length_negative_for_sell_streak() -> None:
    assert chip_streaks.streak_length([-100.0, -50.0, -200.0]) == -3


def test_streak_length_stops_at_first_sign_flip() -> None:
    # A down day two steps back breaks the streak — only the trailing 2 buy days count.
    assert chip_streaks.streak_length([100.0, -10.0, 200.0, 50.0]) == 2


def test_streak_length_zero_on_flat_or_empty_series() -> None:
    assert chip_streaks.streak_length([100.0, 0.0]) == 0
    assert chip_streaks.streak_length([]) == 0
