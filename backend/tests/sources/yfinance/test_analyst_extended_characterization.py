"""Characterization of `_analyst_consensus_extended`: every optional yfinance sub-frame fills its
own keys independently and a missing / empty / non-frame sub-frame contributes nothing. Synthetic
frames only (no network)."""

from __future__ import annotations

import pandas as pd

from sources.yfinance.analyst import _analyst_consensus_extended


def _frames() -> dict[str, object]:
    return {
        "recommendations_summary": pd.DataFrame(
            [
                {"period": "0m", "strongBuy": 9, "buy": 24, "hold": 1, "sell": 2, "strongSell": 3},
                {"period": "-1m", "strongBuy": 1, "buy": 1, "hold": 1, "sell": 1, "strongSell": 1},
            ]
        ),
        "upgrades_downgrades": pd.DataFrame(
            {"Firm": ["Old Firm", "New Firm"], "Action": ["down", "up"]},
            index=pd.to_datetime(["2024-01-01", "2024-03-01"]),
        ),
        "earnings_estimate": pd.DataFrame({"avg": [1.5, 9.0]}, index=["0y", "+1y"]),
        "revenue_estimate": pd.DataFrame({"avg": [1000.0]}, index=["0y"]),
        "growth_estimates": pd.DataFrame(
            {"stockTrend": [0.11, 0.22, 0.33]}, index=["0y", "+1y", "LTG"]
        ),
        "eps_trend": pd.DataFrame(
            {
                "current": [2.0],
                "7daysAgo": [1.9],
                "30daysAgo": [1.8],
                "60daysAgo": [1.7],
                "90daysAgo": [1.6],
            },
            index=["0y"],
        ),
        "eps_revisions": pd.DataFrame({"upLast30days": [4], "downLast30days": [1]}, index=["0y"]),
    }


def test_every_frame_populated_fills_every_key_in_order() -> None:
    extended = _analyst_consensus_extended(_frames())

    assert extended == {
        "strong_buy": 9,
        "buy": 24,
        "hold": 1,
        "sell": 2,
        "strong_sell": 3,
        "latest_upgrade_downgrade_firm": "New Firm",
        "latest_upgrade_downgrade_action": "up",
        "forward_eps_estimate": 1.5,
        "forward_revenue_estimate": 1000.0,
        "long_term_growth_estimate": 0.33,
        "current_year_growth_estimate": 0.11,
        "next_year_growth_estimate": 0.22,
        "eps_trend_current": 2.0,
        "eps_trend_7_days_ago": 1.9,
        "eps_trend_30_days_ago": 1.8,
        "eps_trend_60_days_ago": 1.7,
        "eps_trend_90_days_ago": 1.6,
        "eps_revisions_up_last_30_days": 4,
        "eps_revisions_down_last_30_days": 1,
    }
    assert list(extended)[:6] == [
        "strong_buy",
        "buy",
        "hold",
        "sell",
        "strong_sell",
        "latest_upgrade_downgrade_firm",
    ]


def test_no_frames_gives_an_empty_dict() -> None:
    assert _analyst_consensus_extended({}) == {}


def test_empty_none_and_non_frame_values_contribute_nothing() -> None:
    frames: dict[str, object] = {
        "recommendations_summary": pd.DataFrame(),
        "upgrades_downgrades": pd.DataFrame(),
        "earnings_estimate": None,
        "revenue_estimate": {"0y": {"avg": 1.0}},  # not a DataFrame
        "growth_estimates": "oops",
        "eps_trend": [],
        "eps_revisions": pd.DataFrame(),
    }

    assert _analyst_consensus_extended(frames) == {}


def test_frames_missing_the_wanted_row_label_contribute_nothing() -> None:
    frames: dict[str, object] = {
        "earnings_estimate": pd.DataFrame({"avg": [1.0]}, index=["+1y"]),
        "revenue_estimate": pd.DataFrame({"avg": [1.0]}, index=["-1y"]),
        "growth_estimates": pd.DataFrame({"stockTrend": [0.5]}, index=["industry"]),
        "eps_trend": pd.DataFrame({"current": [1.0]}, index=["+1y"]),
        "eps_revisions": pd.DataFrame({"upLast30days": [1]}, index=["+1y"]),
    }

    assert _analyst_consensus_extended(frames) == {}


def test_growth_rows_are_independent_of_one_another() -> None:
    only_next = {"growth_estimates": pd.DataFrame({"stockTrend": [0.4]}, index=["+1y"])}
    only_ltg = {"growth_estimates": pd.DataFrame({"stockTrend": [0.7]}, index=["LTG"])}

    assert _analyst_consensus_extended(only_next) == {"next_year_growth_estimate": 0.4}
    assert _analyst_consensus_extended(only_ltg) == {"long_term_growth_estimate": 0.7}


def test_a_nan_cell_degrades_that_key_to_none_and_a_missing_column_too() -> None:
    frames: dict[str, object] = {
        "earnings_estimate": pd.DataFrame({"avg": [float("nan")]}, index=["0y"]),
        "revenue_estimate": pd.DataFrame({"other": [5.0]}, index=["0y"]),
        "recommendations_summary": pd.DataFrame([{"period": "0m", "strongBuy": 2}]),
        "upgrades_downgrades": pd.DataFrame(
            {"Firm": [None], "Action": ["init"]}, index=pd.to_datetime(["2024-03-01"])
        ),
    }

    assert _analyst_consensus_extended(frames) == {
        "strong_buy": 2,
        "buy": None,
        "hold": None,
        "sell": None,
        "strong_sell": None,
        "latest_upgrade_downgrade_firm": None,
        "latest_upgrade_downgrade_action": "init",
        "forward_eps_estimate": None,
        "forward_revenue_estimate": None,
    }
