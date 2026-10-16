"""`mcp.search` - weighted ranking behind `search_tools`."""

from __future__ import annotations

from mcp.search import Candidate, rank, score

CATALOG = [
    Candidate("get_chip_flows", "Institutional and broker-branch chip flows (籌碼) for a stock."),
    Candidate("get_us_macro_dashboard", "US macro dashboard: CPI inflation, unemployment, rates."),
    Candidate("get_dividend_history", "Cash and stock dividend history (股利) for a symbol."),
    Candidate("get_margin_balance", "Margin purchase and short sale balances (融資融券)."),
]


def names(intent: str) -> list[str]:
    return [c.name for c in rank(intent, CATALOG, limit=5)]


def test_name_word_beats_description_mention() -> None:
    assert names("chip flows")[0] == "get_chip_flows"


def test_plural_and_gerund_forms_match() -> None:
    assert names("dividends")[0] == "get_dividend_history"
    assert names("flowing chips")[0] == "get_chip_flows"


def test_stopwords_and_underscores_do_not_matter() -> None:
    assert names("what is the US macro dashboard") == ["get_us_macro_dashboard"]
    assert names("get_margin_balance")[0] == "get_margin_balance"


def test_chinese_intent_matches_chinese_descriptions() -> None:
    assert names("融資")[0] == "get_margin_balance"
    assert names("我想看股利")[0] == "get_dividend_history"


def test_no_overlap_returns_nothing() -> None:
    assert names("zzzz qqqq") == []


def test_ties_break_alphabetically_for_stable_output() -> None:
    twins = [Candidate("b_tool", "alpha"), Candidate("a_tool", "alpha")]
    assert [c.name for c in rank("alpha", twins, limit=5)] == ["a_tool", "b_tool"]


def test_limit_is_applied() -> None:
    assert len(rank("history balance", CATALOG, limit=1)) == 1
    assert score("anything", Candidate("x", "y")) == 0
