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
