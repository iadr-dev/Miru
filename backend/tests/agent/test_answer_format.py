"""The final answer never carries a 'TL;DR' restatement, whatever the model did."""

from __future__ import annotations

import pytest

from agent.answer_format import strip_tldr


@pytest.mark.parametrize(
    "line",
    [
        "TL;DR: ETF 是一種基金。",
        "**TL;DR：** ETF 是一種基金。",
        "tldr: ETF 是一種基金。",
        "- TL;DR ETF 是一種基金。",
        "TL;DR：ETF 就是「可在盤中像股票交易的一籃子資產組合」。",
    ],
)
def test_a_tldr_line_is_removed(line: str) -> None:
    text = f"ETF 是在交易所交易的基金。\n\n{line}\n\n以上僅為資料觀察，不構成買賣建議。"
    out = strip_tldr(text)
    assert "TL" not in out.upper().replace("TOTAL", "")
    assert out == "ETF 是在交易所交易的基金。\n\n以上僅為資料觀察，不構成買賣建議。"


def test_an_answer_without_one_is_untouched() -> None:
    text = "ETF 是基金。\n\n- 第一點\n- 第二點\n"
    assert strip_tldr(text) == text


def test_the_word_inside_a_sentence_is_kept() -> None:
    text = "這份報告不需要 TL;DR 這種摘要。"
    assert strip_tldr(text) == text


from agent.answer_format import recover_textual_tool_call  # noqa: E402

KNOWN = {"propose_watchlist_change", "ask_user"}


def test_a_reply_that_is_just_a_tool_call_is_recovered() -> None:
    text = (
        '{"name": "propose_watchlist_change", "arguments": {"action": "add", "symbols": ["2330"]}}'
    )
    assert recover_textual_tool_call(text, KNOWN) == (
        "propose_watchlist_change",
        {"action": "add", "symbols": ["2330"]},
    )


def test_a_fenced_or_namespaced_call_is_recovered_too() -> None:
    fenced = '```json\n{"name": "google:ask_user", "arguments": {"question": "哪一檔？"}}\n```'
    assert recover_textual_tool_call(fenced, KNOWN) == ("ask_user", {"question": "哪一檔？"})


@pytest.mark.parametrize(
    "text",
    [
        "好的，我已經列出提案。",  # ordinary prose
        '這是範例：{"name": "ask_user", "arguments": {}}',  # prose around it
        '{"name": "delete_everything", "arguments": {}}',  # not a tool this turn has
        '{"name": "ask_user", "arguments": [1, 2]}',  # arguments not an object
        '{"name": "ask_user", "arguments": {}, "extra": 1}',  # extra keys
        '{"arguments": {}}',  # no name
        "{not json}",
        "",
    ],
)
def test_anything_else_is_left_alone(text: str) -> None:
    assert recover_textual_tool_call(text, KNOWN) is None
