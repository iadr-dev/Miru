"""The phone layouts of the morning brief, a scheduled report and the daily digest: what each one
keeps, that nothing is cut inside a word, and that every text passes the output scan."""

from __future__ import annotations

import pytest

from governance.textscan import check_output
from scheduler.delivery.layout import Layout, render_html, render_text
from scheduler.notification_messages import (
    DIGEST_MAX_LINES,
    brief_layout,
    brief_preview,
    digest_body,
    digest_layout,
    plain_lines,
    report_layout,
    report_preview,
)
from schemas.alerts import NON_ADVICE_FOOTER
from schemas.schedule_templates import (
    DEFAULT_SCHEDULE_LABEL,
    SCHEDULE_TEMPLATES,
    schedule_label,
)


def _text(layout: Layout) -> str:
    return render_text(layout, NON_ADVICE_FOOTER)


def _scan_clean(text: str) -> bool:
    return check_output(text, has_market_data=False).reason is None


_BRIEF = "\n".join(
    [
        "**【晨間摘要】2026-10-05（星期一）08:30 台北**",
        "",
        "### Overnight Snapshot 隔夜概況",
        "| 資產 | 收盤 |",
        "",
        "### Top 3 Drivers 三大重點",
        "",
        "1. 費城半導體指數 +2.4%（13,136.67） → 半導體產業的美股參考指數 → 相關：2330 台積電",
        "2. 台積電 ADR（TSM） +3.0%（472.78） → 台積電的存託憑證",
        "3. VIX -6.5%（15.31）",
        "4. 第四項不該出現",
        "",
        "### 台股 Pre-Open",
    ]
)


def test_the_brief_layout_is_one_dated_heading_the_fact_of_each_driver_and_a_pointer() -> None:
    layout = brief_layout(_BRIEF)
    assert _text(layout) == (
        "🌅 晨間摘要 2026-10-05（星期一）\n"
        "• 費城半導體指數 +2.4%（13,136.67）\n"
        "• 台積電 ADR（TSM） +3.0%（472.78）\n"
        "• VIX -6.5%（15.31）\n\n"
        "完整報告請見 Miru\n\n"
        f"{NON_ADVICE_FOOTER}"
    )
    assert "【晨間摘要】" not in _text(layout)


@pytest.mark.parametrize(
    ("report", "heading", "bullets"),
    [
        (
            "**【晨間摘要】2026-10-05（星期一）08:30 台北**\n\n### Top 3 Drivers\n\n暫無資料",
            "🌅 晨間摘要 2026-10-05（星期一）",
            0,
        ),
        (
            "**【晨間摘要】2026-10-05 08:30 台北**\n### Top 3 Drivers\n1. 甲 → 乙",
            "🌅 晨間摘要 2026-10-05",
            1,
        ),
        ("沒有標題的內容", "🌅 晨間摘要", 0),
        ("", "🌅 晨間摘要", 0),
    ],
)
def test_a_brief_without_drivers_or_a_headline_still_has_a_heading_and_a_pointer(
    report: str, heading: str, bullets: int
) -> None:
    layout = brief_layout(report)
    assert layout.blocks[0].head == heading
    assert len(layout.blocks[0].lines) == bullets
    assert layout.blocks[-1].lines == ("完整報告請見 Miru",)


def test_the_brief_plain_body_is_the_headline_and_the_drivers_as_before() -> None:
    assert brief_preview(_BRIEF).splitlines() == [
        "【晨間摘要】2026-10-05（星期一）08:30 台北",
        *_BRIEF.splitlines()[7:10],
    ]


_HEAVY = "\n".join(
    [
        "# 2330 台積電 個股報告",
        "",
        "---",
        "",
        "## 一、**基本識別**",
        "| 項目 | 數值 |",
        "|---|---|",
        "| 收盤價 | 1,020 |",
        "",
        "- **營收**年增 12.3%，連續三季成長。其餘細節見下表。",
        "* 參考 [公開資訊觀測站](https://mops.twse.com.tw/x) 的資料",
        "1. _斜體_ 與 `code` 與 ~~刪除~~",
        "```",
        "print('不該出現')",
        "```",
        "> 引言行",
    ]
)


def test_markdown_is_stripped_before_a_report_is_cut_into_lines() -> None:
    assert plain_lines(_HEAVY) == [
        "2330 台積電 個股報告",
        "一、基本識別",
        "營收年增 12.3%，連續三季成長。其餘細節見下表。",
        "參考 公開資訊觀測站 的資料",
        "斜體 與 code 與 刪除",
        "引言行",
    ]


def test_a_heavy_markdown_report_becomes_three_clean_bullets_and_a_pointer() -> None:
    layout = report_layout(_HEAVY)
    assert _text(layout) == (
        "📰 排程摘要\n"
        "• 2330 台積電 個股報告\n"
        "• 一、基本識別\n"
        "• 營收年增 12.3%，連續三季成長。其餘細節見下表。\n\n"
        "完整報告請見 Miru\n\n"
        f"{NON_ADVICE_FOOTER}"
    )
    for leaked in ("**", "###", "|", "---", "](", "```"):
        assert leaked not in _text(layout)


@pytest.mark.parametrize(
    ("skill_ref", "heading"),
    [
        ("stock-report-tw", "📰 台股個股報告"),
        ("chip-flow-review", "📰 籌碼回顧"),
        ("watchlist-review", "📰 自選股週回顧"),
        ("etf-report", "📰 ETF 報告"),
        ("some-user-skill", "📰 排程摘要"),
    ],
)
def test_a_report_is_headed_by_its_schedule_label_and_unknown_skills_keep_the_generic_one(
    skill_ref: str, heading: str
) -> None:
    layout = report_layout(_HEAVY, schedule_label(skill_ref))
    assert layout.blocks[0].head == heading
    assert _text(layout).startswith(f"{heading}\n• ")
    assert _scan_clean(_text(layout))


def test_every_template_has_a_label_and_a_skill_has_one_label() -> None:
    labels: dict[str, set[str]] = {}
    for template in SCHEDULE_TEMPLATES.values():
        assert template.label
        assert _scan_clean(f"📰 {template.label}")
        labels.setdefault(template.skill_ref, set()).add(template.label)
    assert all(len(found) == 1 for found in labels.values())
    assert schedule_label("") == DEFAULT_SCHEDULE_LABEL == "排程摘要"


@pytest.mark.parametrize(
    "report", ["", "   \n\n", "| a | b |\n|---|---|\n---", "```\ncode only\n```"]
)
def test_a_report_with_nothing_readable_has_only_the_heading_and_the_pointer(report: str) -> None:
    layout = report_layout(report)
    assert [b.lines for b in layout.blocks] == [(), ("完整報告請見 Miru",)]
    assert _text(layout).startswith("📰 排程摘要\n\n完整報告請見 Miru")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("短句。", ["• 短句。"]),
        ("第一句結束。" + "後面很長" * 30, ["• 第一句結束。"]),
        ("前段說明，" + "字" * 90, ["• 前段說明…"]),
        (
            "alpha beta gamma " * 8,
            ["• alpha beta gamma alpha beta gamma alpha beta gamma alpha beta gamma alpha beta…"],
        ),
        ("字" * 200, ["• " + "字" * 80 + "…"]),
    ],
)
def test_a_long_line_is_cut_at_a_sentence_clause_or_space_and_never_dropped(
    line: str, expected: list[str]
) -> None:
    assert list(report_layout(line).blocks[0].lines) == expected


def test_a_very_long_report_still_yields_at_most_three_short_bullets() -> None:
    report = "\n".join(f"第 {i} 段：內容很長。" + "字" * 500 for i in range(500))
    layout = report_layout(report)
    assert len(layout.blocks[0].lines) == 3
    assert all(len(line) <= 90 for line in layout.blocks[0].lines)
    assert len(_text(layout)) < 400


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        ("一\n二", "• 一\n• 二\n完整報告請見 Miru"),
        ("", "完整報告請見 Miru"),
        ("| a | b |\n|---|---|\n---", "完整報告請見 Miru"),
        ("字" * 300, "• " + "字" * 80 + "…\n完整報告請見 Miru"),
        (
            _HEAVY,
            "• 2330 台積電 個股報告\n"
            "• 一、基本識別\n"
            "• 營收年增 12.3%，連續三季成長。其餘細節見下表。\n"
            "完整報告請見 Miru",
        ),
    ],
)
def test_the_report_plain_preview_is_the_layout_lines_and_the_pointer(
    report: str, expected: str
) -> None:
    assert report_preview(report) == expected
    layout = report_layout(report)
    assert report_preview(report).split("\n") == [*layout.blocks[0].lines, *layout.blocks[1].lines]


def test_the_report_plain_preview_leaks_no_markdown_and_ends_on_a_whole_line() -> None:
    preview = report_preview(_HEAVY)
    for leaked in ("**", "###", "|", "---", "](", "```", "_斜"):
        assert leaked not in preview
    assert all(len(line) <= 90 for line in preview.split("\n"))


def _titles(n: int) -> list[str]:
    return [f"提醒 {i}" for i in range(n)]


@pytest.mark.parametrize(
    ("count", "alerts", "jobs", "expected"),
    [
        (
            0,
            0,
            2,
            "🗒 過去 24 小時沒有新的通知\n\n目前啟用中：0 個提醒、2 個排程",
        ),
        (
            2,
            1,
            0,
            "🗒 過去 24 小時 2 則通知\n• 提醒 0\n• 提醒 1\n\n目前啟用中：1 個提醒、0 個排程",
        ),
    ],
)
def test_the_digest_layout_groups_the_titles_and_the_active_counts(
    count: int, alerts: int, jobs: int, expected: str
) -> None:
    assert _text(digest_layout(_titles(count), alerts, jobs)) == (
        f"{expected}\n\n{NON_ADVICE_FOOTER}"
    )


@pytest.mark.parametrize("count", [DIGEST_MAX_LINES, DIGEST_MAX_LINES + 1, 30])
def test_a_long_digest_lists_the_newest_titles_in_order_and_counts_the_rest(count: int) -> None:
    titles = _titles(count)
    layout = digest_layout(titles, 3, 4)
    lines = layout.blocks[0].lines
    shown = [line.removeprefix("• ") for line in lines if line.startswith("• ")]
    assert shown == titles[-DIGEST_MAX_LINES:]
    omitted = count - DIGEST_MAX_LINES
    assert (f"…另有 {omitted} 則較早的通知，請見通知紀錄" in lines) is (omitted > 0)
    assert layout.blocks[0].head == f"🗒 過去 24 小時 {count} 則通知"


def test_the_digest_plain_body_keeps_its_wording() -> None:
    assert digest_body(_titles(1), 1, 2) == (
        "過去 24 小時共 1 則通知：\n・提醒 0\n目前啟用中：1 個提醒、2 個排程。\n"
        + NON_ADVICE_FOOTER
    )
    assert digest_body([], 0, 1).startswith("過去 24 小時沒有新的通知。\n")


def test_titles_with_markup_characters_are_escaped_in_html_and_literal_in_text() -> None:
    layout = digest_layout(["A&B <C> 'D'"], 0, 0)
    assert "• A&amp;B &lt;C&gt; 'D'" in render_html(layout, NON_ADVICE_FOOTER)
    assert "• A&B <C> 'D'" in _text(layout)


def test_every_generated_text_passes_the_output_scan() -> None:
    texts = [
        _text(brief_layout(_BRIEF)),
        _text(brief_layout("")),
        brief_preview(_BRIEF),
        _text(report_layout(_HEAVY)),
        _text(report_layout("")),
        f"{report_preview(_HEAVY)}\n{NON_ADVICE_FOOTER}",
        _text(digest_layout(_titles(0), 0, 0)),
        _text(digest_layout(_titles(30), 5, 6)),
        digest_body(_titles(30), 5, 6),
        digest_body([], 0, 0),
    ]
    assert all(_scan_clean(text) for text in texts)
