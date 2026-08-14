"""The non-negotiable governance hook: `check_tool_call` (no full-market scan) and
`check_output` (no trading language, caveat presence) — tested directly, independent of the
loop that wires them in."""

from __future__ import annotations

import pytest

from agent.guardrails import check_output, check_tool_call, scan_structured_payload

pytestmark = pytest.mark.governance


# ---- check_tool_call: no full-market scan ----


@pytest.mark.parametrize(
    "arguments",
    [
        {"scan_all": True},
        {"full_market": "TW"},
        {"whole_market": True},
        {"market_wide": True},
        {"all_symbols": ["2330", "2317"]},
    ],
)
def test_check_tool_call_rejects_full_market_scan_keys(arguments: dict[str, object]) -> None:
    verdict = check_tool_call("some_tool", arguments)
    assert verdict.allowed is False
    assert "full-market scan" in (verdict.reason or "")


@pytest.mark.parametrize("value", ["*", "all", "market", "ALL"])
def test_check_tool_call_rejects_full_market_scan_values(value: str) -> None:
    verdict = check_tool_call("some_tool", {"symbol": value})
    assert verdict.allowed is False


def test_check_tool_call_rejects_too_many_symbols_in_one_call() -> None:
    verdict = check_tool_call("some_tool", {"symbols": [f"S{i}" for i in range(21)]})
    assert verdict.allowed is False


def test_check_tool_call_allows_a_single_bounded_symbol_market_pair() -> None:
    verdict = check_tool_call("get_price_quote", {"id": "2330", "market": "TW"})
    assert verdict.allowed is True
    assert verdict.reason is None


# ---- check_output: non-trading + caveat enforcement ----


@pytest.mark.parametrize(
    "text",
    [
        "You should buy this stock now.",
        "We recommend selling immediately.",
        "I recommend holding for now.",
        "This has a strong buy rating.",
        "Our price target is $50.",
        "PT of $600 on TSMC.",
        "Consider a position sizing of 10% of your portfolio.",
        "How many shares to buy at this level?",
        "目標價 600 元, 買進建議。",
    ],
)
def test_check_output_redacts_trading_language(text: str) -> None:
    verdict = check_output(text)
    assert verdict.allowed is True  # output checks never hard-block
    assert verdict.redacted_text is not None
    assert "[redacted" in verdict.redacted_text
    assert verdict.reason == "trading_language_redacted"


def test_check_output_redacts_only_the_offending_sentence_in_chinese_prose() -> None:
    """A Chinese-language answer is many sentences, not one — only the sentence carrying
    trading language should be redacted, leaving the surrounding analysis intact."""
    text = (
        "鴻海（2317）的上游供應商包含多家半導體廠商。目標價 600 元，買進建議。"
        "近期營收成長主要來自組裝業務擴張。"
    )
    verdict = check_output(text)
    assert verdict.allowed is True
    assert verdict.redacted_text is not None
    assert "上游供應商包含多家半導體廠商" in verdict.redacted_text
    assert "近期營收成長主要來自組裝業務擴張" in verdict.redacted_text
    assert "目標價" not in verdict.redacted_text
    assert "[redacted" in verdict.redacted_text


def test_check_output_leaves_institutional_flow_prose_alone() -> None:
    """Bare 'bought'/'sold' describing institutional flows that already happened is not
    advice-shaped and must survive untouched (aside from caveat-appending)."""
    text = "Foreign institutional investors bought 12,000 lots and sold 3,000 lots yesterday."
    verdict = check_output(text)
    assert verdict.allowed is True
    if verdict.redacted_text is not None:
        assert "[redacted" not in verdict.redacted_text
        assert "bought 12,000 lots" in verdict.redacted_text


def test_check_output_appends_caveat_when_market_data_present_and_caveat_missing() -> None:
    verdict = check_output("Foreign investors increased holdings this week.")
    assert verdict.allowed is True
    assert verdict.redacted_text is not None
    assert "guaranteed real-time" in verdict.redacted_text.lower()
    assert verdict.reason == "caveat_appended"


def test_check_output_does_not_duplicate_an_existing_caveat() -> None:
    text = "This is analytical context only, not investment advice. Not real-time data."
    verdict = check_output(text)
    assert verdict.allowed is True
    assert verdict.redacted_text is None


def test_check_output_skips_caveat_when_no_market_data_involved() -> None:
    verdict = check_output("Hello, how can I help you today?", has_market_data=False)
    assert verdict.allowed is True
    assert verdict.redacted_text is None


def test_check_output_handles_empty_text() -> None:
    verdict = check_output("", has_market_data=False)
    assert verdict.allowed is True


# ---- scan_structured_payload: the card-payload analogue of the prose scan ----


def test_scan_structured_payload_leaves_clean_data_untouched() -> None:
    payload = {"symbol": "2330", "close": 101.0, "rows": [{"name": "台積電"}]}
    cleaned, redacted = scan_structured_payload(payload)
    assert redacted is False
    assert cleaned == payload


def test_scan_structured_payload_redacts_trading_language_in_nested_strings() -> None:
    payload = {"note": "You should buy 2330 now.", "nested": ["strong buy", "neutral fact"]}
    cleaned, redacted = scan_structured_payload(payload)
    assert redacted is True
    assert isinstance(cleaned, dict)
    assert "buy" not in str(cleaned["note"]).lower()
    assert "redacted" in str(cleaned["note"]).lower()
    nested = cleaned["nested"]
    assert isinstance(nested, list)
    assert "redacted" in str(nested[0]).lower()
    assert nested[1] == "neutral fact"


def test_scan_structured_payload_passes_through_non_text_scalars() -> None:
    cleaned, redacted = scan_structured_payload(101.0)
    assert redacted is False
    assert cleaned == 101.0
