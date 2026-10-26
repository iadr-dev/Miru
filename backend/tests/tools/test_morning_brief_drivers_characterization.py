"""Characterization of `morning_brief_render._drivers`: which rows are eligible, how each is
worded, how it is scored, and the top-3 cut. Synthetic figures only."""

from __future__ import annotations

from tests.tools.test_morning_brief_render import _brief, _row
from tools.morning_brief_render import _drivers


def _only(**overrides: object) -> list[str]:
    """Drivers of a brief with every source section emptied except the overrides."""
    empty: dict[str, object] = {"us_overnight": (), "tw_market": (), "institutions": ()}
    empty.update(overrides)
    return _drivers(_brief(**empty))


def test_us_rows_are_worded_by_kind_and_unclassified_or_changeless_rows_are_skipped() -> None:
    lines = _only(
        us_overnight=(
            _row("S&P 500", "7,000.00", 1.0),
            _row("VIX", "20.00", 15.0),
            _row("費城半導體指數", "13,000.00", -4.0),
            _row("美國10年期公債殖利率", "4.20%", 3.0),
            _row("黃金", "2,400", 9.0),  # not a driver kind
            _row("NASDAQ", "20,000", None),  # no change figure
        )
    )

    assert lines == [
        "費城半導體指數 -4.0%（13,000.00） → 半導體產業的美股參考指數 → "
        "相關：台積電 ADR 與台股半導體權值股",
        "VIX +15.0%（20.00） → S&P 500 選擇權隱含波動的指標 → 相關：美股大盤",
        "美國10年期公債殖利率 +3.0%（4.20%） → 美國公債 10 年期殖利率 → 相關：美債與利率敏感資產",
    ]  # scores: SOX 2.0, VIX 1.5, yield 1.5 (tie keeps input order); S&P 0.5 is cut


def test_taiwan_rows_only_count_adr_and_taiex_with_a_change() -> None:
    lines = _only(
        tw_market=(
            _row("加權指數 TAIEX", "48,000", 1.5, "前一交易日收盤"),
            _row("台積電 ADR（TSM）", "470", -3.0, "美股前一交易日收盤"),
            _row("櫃買指數", "250", 8.0),  # neither ADR nor TAIEX
            _row("台指期 TAIEX 夜盤", "48,100", None),  # no change figure
        )
    )

    # both score 1.5 (TAIEX 1.5/1.0, ADR 3.0/2.0): the tie keeps input order
    assert lines == [
        "加權指數 TAIEX +1.5%（48,000） → 台股大盤 → 相關：台股現貨與大盤 ETF",
        "台積電 ADR（TSM） -3.0%（470） → 台積電在美國掛牌的存託憑證，"
        "為前一交易日收盤 → 相關：台股 2330",
    ]


def test_institution_flows_use_the_amount_direction_and_the_note_date() -> None:
    lines = _only(
        institutions=(
            _row("外資", "-120.5 億元", note="2026-10-02 買賣超"),
            _row("投信", "+10.0 億元", note="2026-10-02 買賣超"),
            _row("自營商", "1,000.0 億元", note="2026-10-02 買賣超"),
            _row("其他", "N/A", note="2026-10-02 買賣超"),  # not a number: skipped
        )
    )

    assert lines == [
        "自營商買超 1,000.0 億元（2026-10-02） → 三大法人單日買賣超 → 相關：台股現貨",
        "外資賣超 120.5 億元（2026-10-02） → 三大法人單日買賣超 → 相關：台股現貨",
        "投信買超 10.0 億元（2026-10-02） → 三大法人單日買賣超 → 相關：台股現貨",
    ]


def test_only_the_three_highest_scores_survive_and_ties_keep_input_order() -> None:
    lines = _only(
        us_overnight=(
            _row("S&P 500", "1", 2.0),  # score 2.0
            _row("DOW", "1", 2.0),  # score 2.0, later than S&P
            _row("NASDAQ", "1", 1.0),  # 1.0
            _row("VIX", "1", 30.0),  # 3.0
        )
    )

    assert [ln.split(" ")[0] for ln in lines] == ["VIX", "S&P", "DOW"]


def test_no_eligible_rows_gives_no_drivers() -> None:
    assert _only() == []
