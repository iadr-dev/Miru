"""The 晨間摘要 text is rendered by code: fixed templates over tool figures, so it cannot drift."""

from __future__ import annotations

from schemas.enums import Market
from tools.morning_brief_models import (
    BarPoint,
    BriefEtf,
    BriefEvent,
    BriefHeadline,
    BriefRow,
    GetMorningBriefOutput,
)
from tools.morning_brief_render import render_morning_report


def _row(label: str, text: str, change: float | None = None, note: str = "") -> BriefRow:
    return BriefRow(label=label, text=text, change_pct=change, note=note)


def _brief(**overrides: object) -> GetMorningBriefOutput:
    base: dict[str, object] = {
        "date_taipei": "2026-10-05",
        "time_taipei": "08:30",
        "us_overnight": (
            _row("S&P 500", "7,722.72", 0.73),
            _row("費城半導體指數", "13,136.67", 2.4),
            _row("VIX", "15.31", -6.5),
        ),
        "macro": (_row("美元/台幣", "31.80", -0.03, "2026-10-02"),),
        "tw_market": (
            _row("加權指數 TAIEX", "48,475.74", 0.25, "前一交易日收盤"),
            _row("台積電 ADR（TSM）", "472.78", 2.96, "美股前一交易日收盤"),
        ),
        "institutions": (
            _row("外資", "+26.2 億元", note="2026-10-02 買賣超"),
            _row("投信", "+57.7 億元", note="2026-10-02 買賣超"),
        ),
        "events_today": (
            BriefEvent(
                name="US ISM Services PMI",
                taipei_time="10/05 22:00",
                et_time="10/05 10:00",
                prior="54.1",
            ),
        ),
        "etfs": (
            BriefEtf(
                symbol="00403A", market=Market.TW, close="10.88", change_pct=0.65, premium_pct=-0.63
            ),
        ),
        "headlines": (BriefHeadline(title="台積電法說會日期公布", source="y"),),
        "unavailable": ("類股與漲跌家數的資料日為 2026-09-24，較最新交易日 2026-10-02 落後",),
    }
    base.update(overrides)
    return GetMorningBriefOutput.model_validate(base)


def test_the_report_has_every_section_in_order_and_the_caveat_once() -> None:
    text = render_morning_report(_brief())
    order = [
        "【晨間摘要】2026-10-05（星期一）08:30 台北",
        "### Overnight Snapshot",
        "### Top 3 Drivers",
        "### 台股 Pre-Open",
        "### US Watch",
        "### ETF Corner",
        "### 新聞標題",
        "### 異常與資料缺口",
        "### 今日觀察點",
    ]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)
    assert text.count("不構成買賣建議") == 1 + 1  # the brief's own caveat line and the footer


def test_top_three_drivers_are_the_largest_moves_by_the_documented_scale() -> None:
    text = render_morning_report(_brief())
    block = text.split("### Top 3 Drivers")[1].split("###")[0]
    lines = [ln for ln in block.splitlines() if ln[:2].rstrip(".").isdigit()]
    assert len(lines) == 3
    # SOX +2.4% on a 2% unit, TSM ADR +2.96% on a 2% unit, VIX -6.5% on a 10% unit, an institution
    # at +57.7 億 on a 50 unit: ADR (1.48), SOX (1.2), 投信 (1.15) beat VIX (0.65) and S&P (0.73).
    assert "台積電 ADR" in lines[0]
    assert "費城半導體指數" in lines[1]
    assert "投信買超 57.7 億元" in lines[2]


def test_missing_figures_are_na_and_gaps_are_listed_not_hidden() -> None:
    text = render_morning_report(
        _brief(tw_market=(_row("加權指數 TAIEX", "N/A"),), institutions=(), sectors_up=())
    )
    assert "三大法人：N/A" in text
    assert "| 加權指數 TAIEX | N/A |" in text
    assert "資料缺口：類股與漲跌家數的資料日為 2026-09-24" in text


def test_no_flags_is_said_plainly_and_flags_are_listed() -> None:
    assert "今日無觸發異常提示" in render_morning_report(_brief())
    flagged = render_morning_report(_brief(flags=("VIX 單日上升 16.0%",)))
    assert "- VIX 單日上升 16.0%" in flagged
    assert "今日無觸發異常提示" not in flagged


def test_the_report_states_facts_only_no_advice_no_prediction_no_tldr() -> None:
    text = render_morning_report(_brief())
    for banned in ("TL;DR", "買進", "賣出", "目標價", "支撐", "壓力", "預期開", "值得關注"):
        assert banned not in text


def test_a_pipe_in_a_cell_cannot_break_a_table() -> None:
    text = render_morning_report(_brief(us_overnight=(_row("A|B", "1", 0.1, "note|x"),), macro=()))
    assert "| A¦B | 1 | +0.1% | note¦x |" in text


def test_chart_series_round_trip_on_the_output_model() -> None:
    brief = _brief(us_change_series=(BarPoint(name="S&P 500", value=0.73),))
    assert brief.us_change_series[0].name == "S&P 500"


def test_a_rounding_to_zero_is_not_shown_as_negative_zero() -> None:
    text = render_morning_report(_brief(macro=(_row("美元/台幣", "31.80", -0.03),)))
    assert "-0.0%" not in text
    assert "| 美元/台幣 | 31.80 | 0.0% |" in text
