"""Which skill the keyword fallback picks for report/compare/schedule phrasings.

The fallback is what routes a turn when the LLM classifier is unavailable, and it once sent an
'AAPL 美股個股完整分析報告' and a '0050 ETF 完整分析報告' to the Taiwan stock report (same generic
words, Taiwan skill first alphabetically) — so each market-specific skill must beat the
generic Taiwan triggers, and compare / schedule / alert phrasings must not be swallowed by a
report skill."""

from __future__ import annotations

import pytest

from orchestrator.classifier import _keyword_fallback
from orchestrator.composition import build_skill_registry


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("幫我做一份 2330 台積電的台股個股完整分析報告", "stock-report-tw"),
        ("幫我做 2330 的完整分析報告", "stock-report-tw"),
        ("幫我做一份 AAPL 蘋果的美股個股完整分析報告", "stock-report-us"),
        ("幫我做一份 0050 的 ETF 完整分析報告", "etf-report"),
        ("幫我做一份 SPY 的美股 ETF 完整分析報告", "etf-report"),
        ("比較台積電 2330 和鴻海 2317，給我並列比較報告", "compare-report"),
        ("比較 0050 和 00878 這兩檔 ETF，給我並列比較報告", "compare-report"),
        ("每天開盤前幫我看一下 2330 和 2317 的簡報", "schedule-proposal"),
        ("2317 收盤價高於 300 的時候提醒我", "alert-proposal"),
        ("收盤後幫我看 2330 的籌碼", "chip-flow-review"),
        ("每週幫我做 0050 的 ETF 完整分析報告", "schedule-proposal"),
        ("每週幫我產生 2330 的台股完整分析報告", "schedule-proposal"),
        ("給我今天的晨間摘要，追蹤 NVDA 2330 00403A", "morning-brief"),
        ("晨報", "morning-brief"),
        ("show me the morning brief", "morning-brief"),
    ],
)
def test_keyword_fallback_routes_each_phrasing_to_its_skill(message: str, expected: str) -> None:
    assert _keyword_fallback(message, build_skill_registry()).skills == (expected,)
