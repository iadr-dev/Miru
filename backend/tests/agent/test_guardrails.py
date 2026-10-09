"""The non-negotiable governance hook: `check_tool_call` (no full-market scan) and
`check_output` (no trading language, caveat presence) — tested directly, independent of the
loop that wires them in."""

from __future__ import annotations

import pytest

from agent.guardrails import (
    REDACTION_NOTICE,
    check_output,
    check_tool_call,
    scan_sentence,
    scan_structured_payload,
    split_sentences_streaming,
)

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
    assert REDACTION_NOTICE in verdict.redacted_text
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
    assert REDACTION_NOTICE in verdict.redacted_text


def test_check_output_collapses_consecutive_redactions_into_one_notice() -> None:
    """Regression guard: a live analyst-consensus answer redacted 3 separate adjacent
    sentences, each with its own copy of the notice stacked back-to-back — repetitive noise,
    not 3 distinct pieces of information. Adjacent violations must collapse into one notice."""
    text = "目標價 600 元。買進建議。強力買進評等。近期營收成長主要來自組裝業務擴張。"
    verdict = check_output(text)
    assert verdict.allowed is True
    assert verdict.redacted_text is not None
    assert verdict.redacted_text.count(REDACTION_NOTICE) == 1
    assert "近期營收成長主要來自組裝業務擴張" in verdict.redacted_text


def test_check_output_leaves_institutional_flow_prose_alone() -> None:
    """Bare 'bought'/'sold' describing institutional flows that already happened is not
    advice-shaped and must survive untouched (aside from caveat-appending)."""
    text = "Foreign institutional investors bought 12,000 lots and sold 3,000 lots yesterday."
    verdict = check_output(text)
    assert verdict.allowed is True
    if verdict.redacted_text is not None:
        assert REDACTION_NOTICE not in verdict.redacted_text
        assert "bought 12,000 lots" in verdict.redacted_text


def test_check_output_leaves_descriptive_buy_sell_signal_prose_alone() -> None:
    """'buy/sell signal' is common, legitimate descriptive institutional-flow/technical-analysis
    terminology (e.g. describing what a net-buy pattern is conventionally read as) — unlike
    'buy/sell rating'/'recommendation'/'call', which are specifically advice-shaped and essentially
    never appear in neutral descriptive prose. A real regression: this false-positived and
    redacted an otherwise-legitimate risk caveat sentence in production."""
    text = "This net institutional buying is sometimes interpreted as a bullish buy signal."
    verdict = check_output(text)
    assert verdict.allowed is True
    if verdict.redacted_text is not None:
        assert REDACTION_NOTICE not in verdict.redacted_text
        assert "buy signal" in verdict.redacted_text


def test_check_output_leaves_decisive_descriptive_comparison_alone() -> None:
    """Governance rule 12 explicitly encourages confident descriptive/comparative judgment
    ("structurally the more volatile of the two", "consistent dividend payer") — this must never
    be caught by the trading-language scanner, since none of it is buy/sell/hold-shaped."""
    text = "A股波動性較高，B股客戶結構較為集中。A股為近5年配息穩定的股利支付者，填息成功率較高。"
    verdict = check_output(text)
    assert verdict.allowed is True
    if verdict.redacted_text is not None:
        assert REDACTION_NOTICE not in verdict.redacted_text
        assert "波動性較高" in verdict.redacted_text


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


def test_check_output_keeps_newlines_and_bullets_when_it_rewrites() -> None:
    """Regression guard: re-joining split sentences used to delete every newline, so a bulleted
    answer collapsed into one run-on line whenever ANY rewrite (even the caveat) happened."""
    text = "提案如下：\n\n- 標的：2330。\n- 條件：收盤價高於 800。\n\n請按建立。"
    verdict = check_output(text)
    assert verdict.redacted_text is not None  # caveat appended → rebuild path ran
    assert verdict.redacted_text.startswith(text)


def test_check_output_keeps_newlines_around_a_redacted_sentence() -> None:
    verdict = check_output("第一行。\n建議買進這檔股票。\n第三行。")
    assert verdict.redacted_text is not None
    assert "目標價" not in verdict.redacted_text
    assert "第一行。\n" in verdict.redacted_text
    assert "\n第三行。" in verdict.redacted_text


def test_check_output_appends_the_caveat_in_the_answers_language() -> None:
    verdict = check_output("外資本週買超 2317。")
    assert verdict.redacted_text is not None
    assert verdict.redacted_text.endswith("以上僅為資料觀察，不構成買賣建議。")
    assert "analytical market context" not in verdict.redacted_text


def test_check_output_recognises_a_chinese_caveat_already_present() -> None:
    assert check_output("外資本週買超 2317。以上不構成投資建議。").redacted_text is None


def test_check_output_skips_caveat_when_no_market_data_involved() -> None:
    verdict = check_output("Hello, how can I help you today?", has_market_data=False)
    assert verdict.allowed is True
    assert verdict.redacted_text is None


def test_check_output_preserves_markdown_link_url_intact() -> None:
    """Regression guard: a bare-`.` sentence split used to chop a URL at every period
    (`https://www.tsmc.com` -> `https://www.` / `tsmc.` / `com`) and the rejoin logic then
    re-inserted spaces between the fragments, corrupting the link into
    `https://www. tsmc. com`. The split must require whitespace after an ASCII terminator, not
    just its presence, so a URL/domain with no market-data caveat trigger survives untouched."""
    text = "官方網站 [www.tsmc.com](https://www.tsmc.com) 提供公司資訊。"
    verdict = check_output(text, has_market_data=False)
    assert verdict.allowed is True
    result = verdict.redacted_text if verdict.redacted_text is not None else text
    assert "[www.tsmc.com](https://www.tsmc.com)" in result
    assert "www. tsmc" not in result


def test_check_output_handles_empty_text() -> None:
    verdict = check_output("", has_market_data=False)
    assert verdict.allowed is True


# ---- check_output: our own data-provider vendor names never reach the user ----


@pytest.mark.parametrize("vendor", ["FinMind", "finmind", "yfinance", "YFinance", "Yahoo"])
def test_check_output_strips_our_own_vendor_names(vendor: str) -> None:
    verdict = check_output(f"以上所有數據均為 {vendor} 資料，非即時報價。")
    assert verdict.allowed is True
    result = verdict.redacted_text if verdict.redacted_text is not None else ""
    assert vendor.lower() not in result.lower()
    assert verdict.reason == "vendor_name_redacted"


def test_check_output_strips_yahoo_aggregated_the_actual_leaking_caveat_text() -> None:
    """Regression guard for the real leak: `sources/yfinance/_shared.py::_CAVEAT` says
    "yfinance-sourced third-party data (Yahoo-aggregated)" — the original vendor-name pattern
    caught "yfinance" but not "Yahoo", so this exact substring reached a live company_profile
    card's `sector_caveat` field unredacted."""
    text = (
        "yfinance-sourced third-party data (Yahoo-aggregated) — reference-only, attributed "
        "content, not our recommendation."
    )
    verdict = check_output(text, has_market_data=False)
    result = verdict.redacted_text if verdict.redacted_text is not None else text
    assert "yahoo" not in result.lower()
    assert "yfinance" not in result.lower()


def test_check_output_leaves_the_real_exchange_and_regulator_names_alone() -> None:
    """TWSE/TPEx/TAIFEX/MOPS/TDCC are the real-world exchanges and official disclosure/
    depository systems, not our backend's own data-provider choice — pointing the user at
    "臺灣證券交易所官網" for authoritative confirmation is encouraged attribution, unlike naming
    our own FinMind/yfinance provider, which the user never asked about."""
    text = (
        "請至 TWSE、TPEx 或 TAIFEX 官網、公開資訊觀測站（MOPS）或集保結算所（TDCC）查核最新公告。"
    )
    verdict = check_output(text, has_market_data=False)
    result = verdict.redacted_text if verdict.redacted_text is not None else text
    for name in ("TWSE", "TPEx", "TAIFEX", "MOPS", "TDCC"):
        assert name in result


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
    assert cleaned["note"] == REDACTION_NOTICE
    nested = cleaned["nested"]
    assert isinstance(nested, list)
    assert nested[0] == REDACTION_NOTICE
    assert nested[1] == "neutral fact"


def test_scan_structured_payload_passes_through_non_text_scalars() -> None:
    cleaned, redacted = scan_structured_payload(101.0)
    assert redacted is False
    assert cleaned == 101.0


def test_scan_structured_payload_strips_vendor_names_from_nested_strings() -> None:
    payload = {"caveat": "資料來源：FinMind", "nested": ["yfinance snapshot", "neutral fact"]}
    cleaned, redacted = scan_structured_payload(payload)
    assert redacted is True
    assert isinstance(cleaned, dict)
    assert "finmind" not in str(cleaned["caveat"]).lower()
    nested = cleaned["nested"]
    assert isinstance(nested, list)
    assert "yfinance" not in str(nested[0]).lower()
    assert nested[1] == "neutral fact"


def test_scan_structured_payload_strips_yahoo_from_a_card_caveat_field() -> None:
    """Regression guard: this is `sources/yfinance/_shared.py::_CAVEAT`'s exact wording, as it
    reaches a `company_profile`/`analyst_consensus`/`esg_score` card's `sector_caveat`/
    `currentness_caveat` field via `agent.loop._build_card`'s `scan_structured_payload` call."""
    payload = {
        "sector_caveat": (
            "yfinance-sourced third-party data (Yahoo-aggregated) — reference-only, "
            "attributed content, not our recommendation."
        )
    }
    cleaned, redacted = scan_structured_payload(payload)
    assert redacted is True
    assert isinstance(cleaned, dict)
    assert "yahoo" not in str(cleaned["sector_caveat"]).lower()


def test_scan_structured_payload_redacts_trading_language_in_a_chart_hint_title() -> None:
    """`chart_hint` (schemas/chart_hint.py) rides through `agent.loop._build_card`'s
    `scan_structured_payload(result.model_dump(mode="json"))` call exactly like every other field
    on a tool's output_model — no new scanning code needed for it. Proves that property: a
    trading-language chart title is redacted by the SAME walk that already scans `sector_caveat`/
    `note` above, not a bespoke chart-specific scanner."""
    payload = {
        "chart_hint": {
            "mark": "bar",
            "title": "目標價 600 元，買進建議",
            "x": {"field": "trade_date", "type": "temporal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
        "rows": [{"trade_date": "2026-01-01", "revenue": 100}],
    }
    cleaned, redacted = scan_structured_payload(payload)
    assert redacted is True
    assert isinstance(cleaned, dict)
    chart_hint = cleaned["chart_hint"]
    assert isinstance(chart_hint, dict)
    assert chart_hint["title"] == REDACTION_NOTICE
    # Sibling fields (mark/x/y/rows) are untouched — only the string leaf matching trading
    # language is redacted, never the whole payload.
    assert chart_hint["mark"] == "bar"
    assert cleaned["rows"] == payload["rows"]


def test_scan_structured_payload_leaves_a_decisive_chart_hint_title_alone() -> None:
    """The governance rule-12 counterpart to the redaction test above: a confident descriptive
    chart title (never buy/sell/hold-shaped) must survive this walk untouched."""
    payload = {
        "chart_hint": {
            "mark": "bar",
            "title": "營收季節性：8月為旺季，2月為連續5年負增長的低谷",
            "x": {"field": "trade_date", "type": "temporal"},
            "y": {"field": "revenue", "type": "quantitative"},
        }
    }
    cleaned, redacted = scan_structured_payload(payload)
    assert redacted is False
    assert cleaned == payload


# ---- split_sentences_streaming: incremental, nothing-discarded boundary detection ----


@pytest.mark.parametrize(
    "buffer",
    [
        "First sentence. Second sentence. Third.",
        "純中文句子。第二句。第三句",
        "Mixed 中文 and English. 第二句 ends here.",
        "A URL https://www.tsmc.com should not split mid-domain.",
        "12.5% is a decimal, not a sentence end.",
        "",
        "No terminator at all",
        "Ends with terminator.",
        "結尾是全角句號。",
    ],
)
def test_split_sentences_streaming_never_discards_a_character(buffer: str) -> None:
    """The core correctness property this function exists for: reassembling every returned
    chunk reproduces the original buffer byte-for-byte, including all whitespace — unlike
    `split_sentences` (which `.strip()`s and swallows inter-sentence whitespace, safe only for
    a one-shot final scan), losing so much as one space here would corrupt a live streaming
    render the next delta can no longer repair."""
    assert "".join(split_sentences_streaming(buffer)) == buffer


def test_split_sentences_streaming_keeps_a_still_growing_tail_as_its_own_chunk() -> None:
    """The first sentence closes (terminator + following space) and splits off; the still-open
    remainder — which could still grow with more text — becomes its own trailing chunk, with
    its leading space intact (nothing discarded, per the reconstruction invariant above)."""
    chunks = split_sentences_streaming("First sentence. Second sen")
    assert chunks == ["First sentence.", " Second sen"]


def test_split_sentences_streaming_splits_full_width_terminator_with_no_space_needed() -> None:
    chunks = split_sentences_streaming("第一句。第二句")
    assert chunks == ["第一句。", "第二句"]


# ---- scan_sentence: streaming per-chunk scan, whitespace-preserving ----


def test_scan_sentence_preserves_a_leading_separator_around_a_redaction() -> None:
    """`scan_sentence` is called on chunks from `split_sentences_streaming`, which (unlike
    `split_sentences`) can carry a leading space (the word-boundary whitespace riding along
    with the start of the next chunk) — a redacted sentence must keep that leading space, not
    silently drop it and glue onto the preceding text with no gap."""
    result = scan_sentence(" You should buy this stock now.")
    assert result.startswith(" ")
    assert REDACTION_NOTICE in result
    assert "buy" not in result.lower()


def test_scan_sentence_leaves_clean_text_untouched() -> None:
    assert scan_sentence(" Foreign investors bought shares today.") == (
        " Foreign investors bought shares today."
    )


def test_scan_sentence_strips_vendor_names() -> None:
    result = scan_sentence(" Data sourced from FinMind.")
    assert "finmind" not in result.lower()


def test_vendor_name_scrub_leaves_cited_urls_intact() -> None:
    """A web-search answer cites real addresses; rewriting the host would break the link, while
    the vendor name in prose is still replaced."""
    text = "來源：[新聞](https://tw.stock.yahoo.com/quote/2330.TW/news)，資料由 FinMind 提供。"
    result = check_output(text).redacted_text or ""
    assert "https://tw.stock.yahoo.com/quote/2330.TW/news" in result
    assert "finmind" not in result.lower()
