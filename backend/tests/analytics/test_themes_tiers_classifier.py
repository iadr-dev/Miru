"""Isolated unit tests for the pure keyword-heuristic tier classifier — no I/O, no DB."""

from __future__ import annotations

import pytest

from analytics.themes.tiers import classify_tier


@pytest.mark.parametrize(
    ("sub_industry", "expected"),
    [
        # Upstream: raw-material / component / manufacturing roles.
        ("晶圓代工", "upstream"),
        ("半導體材料", "upstream"),
        ("封測", "upstream"),
        ("面板", "upstream"),
        ("電池芯", "upstream"),
        # Midstream: design / module / trading roles.
        ("IC設計", "midstream"),
        ("散熱模組", "midstream"),
        ("貿易商", "midstream"),
        # Downstream: end-market / distribution / brand roles.
        ("食品通路", "downstream"),
        ("品牌服飾", "downstream"),
        ("系統整合", "downstream"),
        ("整車", "downstream"),
        # Unmatched -> midstream default (the safest neutral position).
        ("連接器", "midstream"),
        ("", "midstream"),
    ],
)
def test_classify_tier(sub_industry: str, expected: str) -> None:
    assert classify_tier(sub_industry) == expected


def test_downstream_wins_over_upstream_when_both_match() -> None:
    # A segment naming both a component (零組件, upstream) and an application (應用, downstream) is
    # read by its more-informative end-market role.
    assert classify_tier("車用電子零組件應用") == "downstream"
