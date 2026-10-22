"""Characterization of `morning_brief_tools._flags` — the rule-based unusual-condition flags,
with each threshold's inclusive/exclusive edge and each way a row is skipped."""

from __future__ import annotations

from schemas.enums import Market
from tools.morning_brief_models import BriefEtf, BriefRow
from tools.morning_brief_tools import _flags


def _row(label: str, chg: float | None) -> BriefRow:
    return BriefRow(label=label, change_pct=chg)


def _etf(symbol: str, premium: float | None) -> BriefEtf:
    return BriefEtf(symbol=symbol, market=Market.TW, premium_pct=premium)


def test_nothing_unusual_gives_no_flags() -> None:
    us = [_row("VIX", 14.9), _row("SOX", 2.9), _row("S&P 500", 1.9), _row("10Y", 50.0)]
    tw = [_row("TAIEX", 1.9), _row("TSM ADR", -2.9)]
    assert _flags(us, tw, [_etf("0050", 1.0), _etf("0056", None)]) == []
    assert _flags([], [], []) == []


def test_vix_flag_is_inclusive_and_only_upward() -> None:
    assert _flags([_row("VIX", 15.0)], [], []) == ["VIX 單日上升 15.0%"]
    assert _flags([_row("vix index", 20.0)], [], []) == ["VIX 單日上升 20.0%"]
    assert _flags([_row("VIX", -30.0)], [], []) == []
    assert _flags([_row("The VIX", 30.0)], [], []) == []  # startswith, not contains


def test_sox_flag_is_two_sided_and_inclusive() -> None:
    assert _flags([_row("SOX", 3.0)], [], []) == ["費城半導體指數單日 +3.0%"]
    assert _flags([_row("費城 sox", -3.4)], [], []) == ["費城半導體指數單日 -3.4%"]
    assert _flags([_row("SOX", -2.99)], [], []) == []


def test_us_index_flags_use_the_original_label_and_prefix_match() -> None:
    us = [_row("S&P 500", 2.0), _row("Nasdaq", -2.5), _row("Dow", 2.04), _row("Russell", 9.0)]
    assert _flags(us, [], []) == ["S&P 500單日 +2.0%", "Nasdaq單日 -2.5%", "Dow單日 +2.0%"]
    assert _flags([_row("S&P 500", 1.99)], [], []) == []
    assert _flags([_row("E-mini S&P", 5.0)], [], []) == []


def test_us_rows_without_a_change_are_skipped_and_order_is_kept() -> None:
    us = [_row("VIX", None), _row("SOX", 5.0), _row("VIX", 16.0)]
    assert _flags(us, [], []) == ["費城半導體指數單日 +5.0%", "VIX 單日上升 16.0%"]


def test_tw_flags_taiex_and_adr_independent_and_two_sided() -> None:
    tw = [_row("TAIEX", -2.0), _row("TSM ADR", 3.0), _row("Other", 9.0), _row("TAIEX", None)]
    assert _flags([], tw, []) == ["加權指數單日 -2.0%", "台積電 ADR 單日 +3.0%"]
    assert _flags([], [_row("TAIEX ADR", 3.5)], []) == [
        "加權指數單日 +3.5%",
        "台積電 ADR 單日 +3.5%",
    ]
    assert _flags([], [_row("TSM ADR", 2.99)], []) == []
    assert _flags([], [_row("taiex", 5.0)], []) == []  # case-sensitive


def test_etf_premium_flag_is_exclusive_two_sided_and_ordered_last() -> None:
    etfs = [_etf("0050", 1.5), _etf("0056", -1.234), _etf("00878", 1.0), _etf("006208", None)]
    flags = _flags([_row("VIX", 20.0)], [_row("TAIEX", 2.5)], etfs)
    assert flags == [
        "VIX 單日上升 20.0%",
        "加權指數單日 +2.5%",
        "0050 折溢價 +1.50%（絕對值超過 1%）",
        "0056 折溢價 -1.23%（絕對值超過 1%）",
    ]
