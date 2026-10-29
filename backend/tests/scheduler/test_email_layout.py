"""The email a laid-out notification becomes: structured HTML and a text part that are the same
words, every dynamic string escaped, the headers and unsubscribe line untouched, and no layout
meaning the old paragraph-per-block copy."""

from __future__ import annotations

import html
import re

import pytest

from config.settings import Settings
from governance.textscan import check_output
from scheduler.delivery.base import DeliveryNotification, DeliveryRecipient
from scheduler.delivery.channels import EmailChannel
from scheduler.delivery.layout import Layout, LayoutBlock, render_email_html, render_text
from scheduler.notification_messages import (
    brief_layout,
    digest_layout,
    report_layout,
    report_preview,
)
from schemas.alerts import NON_ADVICE_FOOTER

_LINK = "https://miru.example/api/v1/email/unsubscribe?token=u.sig"
_UNSUBSCRIBE = f"不想再收到電子郵件通知？一鍵取消：{_LINK}"
_NAME = "A&B <台積> \"電\" 'x'"

_ALERT = Layout(
    blocks=(
        LayoutBlock(
            head="🔔 2330 台積電",
            lines=(
                "收盤價 1,020，高於設定值 1,000",
                "條件：收盤價高於 1,000",
                "資料日期：2026-10-27",
            ),
        ),
    )
)
_MORNING = Layout(
    blocks=(
        LayoutBlock(head="📋 今日盯盤清單 2026-10-27"),
        LayoutBlock(
            head="波動壓縮",
            lines=tuple(f"{2300 + i} 公司{i}　收盤 {100 + i}" for i in range(29)),
            fold=True,
        ),
    )
)
_MARKUP = Layout(
    blocks=(
        LayoutBlock(head=f"🔔 {_NAME}", lines=(f"{_NAME} 收盤價 1,020", "資料日期：2026-10-27")),
    )
)
_LAYOUTS = {
    "alert": _ALERT,
    "morning-list": _MORNING,
    "report": report_layout("# 報告\n- 營收年增 12.3%，連續三季成長。\n- 毛利率持平。"),
    "brief": brief_layout(
        "**【晨間摘要】2026-10-05（星期一）**\n### Top 3 Drivers\n1. VIX -6.5% → x"
    ),
    "digest": digest_layout(["提醒 1", "提醒 2"], 2, 1),
    "markup": _MARKUP,
}


def _channel() -> EmailChannel:
    return EmailChannel(Settings(resend_api_key="k", email_from_address="a@b.test"))


def _request(
    layout: Layout | None, *, link: str | None = None, body: str = "plain body"
) -> dict[str, object]:
    notification = DeliveryNotification(
        notification_id="n1", title="標題 & <t>", body=body, layout=layout
    )
    recipient = DeliveryRecipient(user_id="u", email_address="a@example.test", unsubscribe_url=link)
    return _channel().build_request(notification, recipient).json


def _visible(document: str) -> list[str]:
    body = document.split("</head>", 1)[1]
    lines = html.unescape(re.sub(r"<[^>]+>", "\n", body)).splitlines()
    return [line.strip() for line in lines if line.strip()]


@pytest.mark.parametrize("name", list(_LAYOUTS))
def test_the_text_part_is_exactly_the_rendered_layout(name: str) -> None:
    layout = _LAYOUTS[name]
    payload = _request(layout)
    assert payload["text"] == render_text(layout, NON_ADVICE_FOOTER)
    assert check_output(str(payload["text"]), has_market_data=False).reason is None


@pytest.mark.parametrize("name", list(_LAYOUTS))
def test_the_html_says_what_the_text_says_in_the_same_order(name: str) -> None:
    payload = _request(_LAYOUTS[name])
    heard = [line for line in str(payload["text"]).splitlines() if line.strip()]
    assert _visible(str(payload["html"])) == heard


def test_each_line_is_its_own_paragraph_and_heads_are_headings() -> None:
    document = str(_request(_ALERT)["html"])
    assert document.count("<h1") == 1
    assert "<h1" in document and "🔔 2330 台積電</h1>" in document
    assert document.count("<p ") == 4  # three lines and the footer
    for line in _ALERT.blocks[0].lines:
        assert f">{line}</p>" in document
    assert document.rstrip().endswith(f"{NON_ADVICE_FOOTER}</p></body></html>")


def test_the_first_head_is_the_h1_and_later_heads_are_h2_and_a_fold_reads_like_any_block() -> None:
    document = str(_request(_MORNING)["html"])
    assert document.count("<h1") == 1
    assert "<h2" in document
    assert "<blockquote" not in document
    assert "<details" not in document
    assert document.count("<p ") == 29 + 1
    assert "<img" not in document


def test_the_document_declares_its_language_title_and_viewport_and_has_no_images() -> None:
    document = str(_request(_ALERT)["html"])
    assert 'lang="zh-Hant"' in document
    assert "<title>標題 &amp; &lt;t&gt;</title>" in document
    assert 'name="viewport"' in document
    assert "<img" not in document


def test_the_title_is_the_h1_only_when_the_layout_has_no_lead_heading() -> None:
    bare = Layout(blocks=(LayoutBlock(lines=("只有一行",)),))
    assert "<h1>標題 &amp; &lt;t&gt;</h1>" in str(_request(bare)["html"])
    assert "<h1>" not in str(_request(_ALERT)["html"])


def test_markup_characters_in_a_name_are_escaped_in_html_and_literal_in_text() -> None:
    payload = _request(_MARKUP)
    document = str(payload["html"])
    escaped = html.escape(_NAME)
    assert f"🔔 {escaped}</h1>" in document
    assert f">{escaped} 收盤價 1,020</p>" in document
    for raw in ("<台積>", '"電"'):
        assert raw not in document
    assert f"🔔 {_NAME}" in str(payload["text"])
    assert render_email_html(_MARKUP, NON_ADVICE_FOOTER) in document


def test_the_subject_headers_and_unsubscribe_line_do_not_change_with_a_layout() -> None:
    plain = _request(None, link=_LINK)
    laid_out = _request(_ALERT, link=_LINK)
    assert laid_out["subject"] == plain["subject"] == "標題 & <t>"
    assert (
        laid_out["headers"]
        == plain["headers"]
        == {
            "List-Unsubscribe": f"<{_LINK}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
    )
    assert str(laid_out["text"]).endswith(f"{NON_ADVICE_FOOTER}\n\n{_UNSUBSCRIBE}")
    document = str(laid_out["html"])
    assert document.index(NON_ADVICE_FOOTER) < document.index("一鍵取消")
    assert f'<a href="{_LINK}" rel="noopener noreferrer"' in document
    assert _visible(document)[-2:] == ["不想再收到電子郵件通知？一鍵取消：", _LINK]
    assert "headers" not in _request(_ALERT)


def test_the_idempotency_key_is_still_the_notification_id() -> None:
    notification = DeliveryNotification(notification_id="n9", title="t", body="b", layout=_ALERT)
    request = _channel().build_request(
        notification, DeliveryRecipient(user_id="u", email_address="a@example.test")
    )
    assert request.headers["Idempotency-Key"] == "n9"


def test_without_a_layout_the_email_is_the_body_in_paragraphs_as_before() -> None:
    payload = _request(None, body="第一段\n第二行\n\n第三段")
    assert payload["text"] == f"第一段\n第二行\n\n第三段\n\n{NON_ADVICE_FOOTER}"
    document = str(payload["html"])
    assert "<h1>標題 &amp; &lt;t&gt;</h1>" in document
    assert "<p>第一段<br>第二行</p><p>第三段</p>" in document


def test_the_scheduled_report_plain_body_is_clean_text_for_an_email_without_a_layout() -> None:
    markdown = "## 報告\n| a | b |\n|---|---|\n- **營收**年增 12.3%，連續三季成長。"
    payload = _request(None, body=report_preview(markdown))
    for leaked in ("##", "**", "|", "---"):
        assert leaked not in str(payload["text"])
    assert check_output(str(payload["text"]), has_market_data=False).reason is None


_TRICKY = 'https://miru.example/u?a=1&b="2"&c=<x>\'y'


def test_an_https_unsubscribe_link_is_an_escaped_anchor_with_the_same_text_part() -> None:
    payload = _request(_ALERT, link=_TRICKY)
    document = str(payload["html"])
    escaped = html.escape(_TRICKY, quote=True)
    assert f'<a href="{escaped}" rel="noopener noreferrer"' in document
    assert f'underline">{escaped}</a>' in document
    assert _TRICKY not in document
    sentence = f"不想再收到電子郵件通知？一鍵取消：{_TRICKY}"
    assert str(payload["text"]).endswith(f"{NON_ADVICE_FOOTER}\n\n{sentence}")
    assert payload["headers"] == {
        "List-Unsubscribe": f"<{_TRICKY}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def test_an_http_unsubscribe_link_stays_plain_text() -> None:
    link = "http://miru.local/unsub?a=1&b=2"
    document = str(_request(_ALERT, link=link)["html"])
    assert "<a " not in document
    assert f"一鍵取消：{html.escape(link)}</p>" in document


def test_no_unsubscribe_link_adds_no_anchor() -> None:
    assert "<a " not in str(_request(_ALERT)["html"])
