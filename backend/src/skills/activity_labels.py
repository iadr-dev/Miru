"""Natural-language, locale-aware activity labels for SSE narration.

`orchestrator.py` templates `f"Running {skill_name}"` as the `agent.spawned` event's
`activity` field today — a literal internal skill id shown to end users (the bug this module
fixes; see the task that introduced it). `_SKILL_ACTIVITY_LABELS` below is the bespoke
id -> label lookup, one entry per skill under `skills/built_ins/`, in the same small
id-keyed-dict style already used for `llm/registry.py`'s `_OPENAI_COMPAT_BASE_URLS` — no
skill-metadata field already carries a short display name (`SkillFrontmatter.description` is a
full sentence, too long for a live "what's happening now" line), so this is a parallel table
rather than a reused field.

`locale` mirrors `agent/prompts/governance.py::governance_prompt`'s own locale-interpolation
precedent: a small locale-keyed structure (here, one dict per locale) rather than a runtime
translation call, since the label set is small, fixed, and known at write time.
"""

from __future__ import annotations

from typing import Final

# zh-TW is this app's default interface locale (see `agent/prompts/governance.py`'s own
# `_DEFAULT_LOCALE`) — every skill under `skills/built_ins/` has an entry here.
_ZH_TW_LABELS: Final[dict[str, str]] = {
    "active-etf-flow": "正在檢視主動式 ETF 操作與資金流向",
    "alert-proposal": "正在擬定提醒提案，待你確認",
    "memory-proposal": "正在擬定記憶提案，待你確認",
    "watchlist-proposal": "正在擬定加入自選股的提案，待你確認",
    "account-overview": "正在查看你的自選股、提醒、用量與通知",
    "watchlist-review": "正在回顧你的自選股表現與近期事件",
    "block-trade-and-broker-flow": "正在檢視鉅額交易與分點進出",
    "catalyst-scan": "正在掃描潛在催化事件",
    "chip-flow-review": "正在檢視籌碼流向",
    "company-profile": "正在查詢個股基本資料",
    "convertible-bond-review": "正在檢視可轉債概況",
    "corporate-actions-and-market-value": "正在檢視公司行動與市值資料",
    "daily-context-brief": "正在整理每日市場摘要",
    "morning-brief": "正在整理晨間摘要",
    "derivatives-positioning": "正在檢視期權籌碼位置",
    "disposition-watch": "正在檢視處置股公告",
    "earnings-quality": "正在檢視營收與財務品質",
    "etf-compare": "正在比較 ETF 資料",
    "etf-overlap-and-comparison": "正在比較 ETF 持股重疊與績效",
    "finmind-stock-news": "正在查詢個股新聞",
    "futures-options-market-data": "正在查詢期貨與選擇權市場資料",
    "governance-risk": "正在檢視公司治理風險指標",
    "industry-chain-money-flow": "正在檢視產業鏈資金流",
    "international-equity-snapshot": "正在查詢海外個股概況",
    "intraday-and-multi-timeframe-price": "正在查詢日內與多時間週期價格",
    "live-snapshot-check": "正在查詢即時報價快照",
    "macro-dashboard": "正在檢視宏觀經濟儀表板",
    "margin-and-short-interest": "正在檢視融資融券與券資比",
    "market-breadth-and-flow": "正在檢視全市場廣度與資金流",
    "reference-data-lookup": "正在查詢參考資料",
    "schedule-proposal": "正在擬定排程提案，待你確認",
    "self-assessed-earnings-check": "正在查詢自結損益公告",
    "stock-report-tw": "正在整理台股個股完整分析報告",
    "stock-report-us": "正在整理美股個股完整分析報告",
    "etf-report": "正在整理 ETF 完整分析報告",
    "compare-report": "正在整理兩檔標的並列比較報告",
    "stock-financial-comparison": "正在比較兩檔股票的財務資料",
    "stock-score": "正在查詢個股評分",
    "stock-screener": "正在執行條件篩選",
    "news-and-sentiment": "正在查詢新聞與討論熱度",
    "market-rankings": "正在查詢全市場排行榜",
    "sector-rotation": "正在檢視類股輪動與熱力圖",
    "event-study": "正在整理事件後的歷史統計",
    "us-macro-data": "正在查詢美國總經資料",
    "technical-trend-read": "正在檢視技術面與趨勢讀數",
    "etf-market-overview": "正在查詢 ETF 市場總覽",
    "tdcc-registry-lookup": "正在查詢集保相關登記資料",
    "theme-lookup": "正在查詢主題與產業鏈分類",
    "valuation-band": "正在檢視估值區間",
    "warrant-and-derivatives-microstructure": "正在查詢權證與期權逐筆/分點資料",
}

# English fallback for a non-zh-TW-locale turn — mirrors the same skill set 1:1.
_EN_LABELS: Final[dict[str, str]] = {
    "active-etf-flow": "Reviewing active ETF activity and money flow",
    "alert-proposal": "Drafting an alert proposal for you to confirm",
    "memory-proposal": "Drafting a memory proposal for you to confirm",
    "watchlist-proposal": "Drafting a watchlist addition for you to confirm",
    "account-overview": "Looking up your watchlists, alerts, usage and notifications",
    "watchlist-review": "Reviewing your watchlist's recent moves and upcoming events",
    "block-trade-and-broker-flow": "Reviewing block trades and broker flow",
    "catalyst-scan": "Scanning for potential catalysts",
    "chip-flow-review": "Reviewing institutional and margin flows",
    "company-profile": "Looking up company profile",
    "convertible-bond-review": "Reviewing convertible bond overview",
    "corporate-actions-and-market-value": "Reviewing corporate actions and market value",
    "daily-context-brief": "Assembling daily market brief",
    "morning-brief": "Assembling the morning brief",
    "derivatives-positioning": "Reviewing derivatives positioning",
    "disposition-watch": "Checking disposition-stock status",
    "earnings-quality": "Reviewing earnings and financial quality",
    "etf-compare": "Comparing ETFs",
    "etf-overlap-and-comparison": "Comparing ETF overlap and performance",
    "finmind-stock-news": "Looking up stock news",
    "futures-options-market-data": "Looking up futures and options market data",
    "governance-risk": "Reviewing governance risk indicators",
    "industry-chain-money-flow": "Reviewing industry-chain money flow",
    "international-equity-snapshot": "Looking up overseas equity snapshot",
    "intraday-and-multi-timeframe-price": "Looking up intraday and multi-timeframe price",
    "live-snapshot-check": "Checking a live quote snapshot",
    "macro-dashboard": "Reviewing the macro dashboard",
    "margin-and-short-interest": "Reviewing margin and short-interest data",
    "market-breadth-and-flow": "Reviewing market-wide breadth and flow",
    "reference-data-lookup": "Looking up reference data",
    "schedule-proposal": "Drafting a scheduled-briefing proposal for you to confirm",
    "self-assessed-earnings-check": "Checking self-assessed earnings filing",
    "stock-report-tw": "Building a full Taiwan stock analysis report",
    "stock-report-us": "Building a full US stock analysis report",
    "etf-report": "Building a full ETF analysis report",
    "compare-report": "Building a side-by-side comparison report",
    "stock-financial-comparison": "Comparing two stocks' financial data",
    "stock-score": "Looking up stock score",
    "stock-screener": "Running the stock screener",
    "news-and-sentiment": "Looking up news and discussion buzz",
    "market-rankings": "Looking up market-wide rankings",
    "sector-rotation": "Reviewing sector rotation and heatmap",
    "event-study": "Reviewing historical statistics after the event",
    "us-macro-data": "Looking up US macro data",
    "technical-trend-read": "Reviewing technical indicators and trend score",
    "etf-market-overview": "Looking up the ETF market overview",
    "tdcc-registry-lookup": "Looking up TDCC registry data",
    "theme-lookup": "Looking up theme/industry-chain classification",
    "valuation-band": "Reviewing valuation band",
    "warrant-and-derivatives-microstructure": "Looking up warrant/derivatives tick and broker data",
}

_LOCALE_LABELS: Final[dict[str, dict[str, str]]] = {
    "zh-TW": _ZH_TW_LABELS,
    "en": _EN_LABELS,
}

_DEFAULT_LOCALE: Final[str] = "zh-TW"


def skill_activity_label(skill_name: str, *, locale: str = _DEFAULT_LOCALE) -> str:
    """A natural-language "what's happening now" phrase for `skill_name`, in `locale`.

    Falls back to the default locale's table when `locale` isn't one of the (currently two)
    known locales, and to a generic templated phrase — never the raw skill id — when
    `skill_name` isn't in the table (a newly added skill that hasn't been given a label yet).
    """
    labels = _LOCALE_LABELS.get(locale, _ZH_TW_LABELS)
    label = labels.get(skill_name)
    if label is not None:
        return label
    fallback_labels = _LOCALE_LABELS[_DEFAULT_LOCALE] if labels is not _ZH_TW_LABELS else labels
    label = fallback_labels.get(skill_name)
    if label is not None:
        return label
    friendly = skill_name.replace("-", " ")
    return f"正在處理：{friendly}" if locale == "zh-TW" else f"Working on: {friendly}"


__all__ = ["skill_activity_label"]
