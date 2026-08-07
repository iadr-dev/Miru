"""Keyword-heuristic tier classifier — the deterministic fallback that assigns a `sub_industry`
to an upstream/midstream/downstream position when the curated editorial map does not cover it.

Pure and I/O-free by design: it takes a `sub_industry` string and returns a tier literal, nothing
more, so it is exhaustively testable in isolation. The classification is substring keyword
membership over Taiwan business vocabulary, checked most-specific-first: an end-market/distribution
role (downstream) is recognized before a raw-material/manufacturing role (upstream), which is
recognized before a design/module/trading role (midstream); a `sub_industry` matching none of these
falls back to midstream, the safest neutral position for an unclassifiable segment.

The order matters because a single term can carry more than one role's keyword — e.g. a segment
naming both a component (upstream) and an application (downstream) is treated as downstream, since
its end-market role is the more informative one for a supply-chain reader.
"""

from __future__ import annotations

from typing import Final, Literal

Tier = Literal["upstream", "midstream", "downstream"]

# End-market / distribution / brand / application roles — checked first so a segment that also
# names a component is still read by its more-informative end-market role.
_DOWNSTREAM_KEYWORDS: Final[tuple[str, ...]] = (
    "通路",
    "零售",
    "品牌",
    "終端",
    "應用",
    "系統整合",
    "系統",
    "服務",
    "電商",
    "銷售",
    "經銷",
    "餐飲",
    "百貨",
    "醫院",
    "整車",
    "整機",
    "成品",
    "客戶",
)

# Raw-material / component / manufacturing-equipment roles.
_UPSTREAM_KEYWORDS: Final[tuple[str, ...]] = (
    "製造",
    "材料",
    "原料",
    "晶圓",
    "設備",
    "零組件",
    "化學品",
    "化學",
    "礦",
    "基板",
    "導線架",
    "光罩",
    "矽智財",
    "晶圓製造",
    "面板",
    "電池芯",
    "封測",
    "封裝",
    "代工",
    "鑄造",
    "上游",
)

# Design / module / agency / processing / trading roles — the middle of the chain.
_MIDSTREAM_KEYWORDS: Final[tuple[str, ...]] = (
    "設計",
    "模組",
    "代理",
    "加工",
    "中游",
    "IC設計",
    "IP",
    "組裝",
    "貿易商",
    "經銷商",
)


def classify_tier(sub_industry: str) -> Tier:
    """Assign `sub_industry` to a supply-chain tier by substring keyword membership. Downstream
    keywords win over upstream, which win over midstream; an unmatched segment defaults to
    midstream."""
    if any(keyword in sub_industry for keyword in _DOWNSTREAM_KEYWORDS):
        return "downstream"
    if any(keyword in sub_industry for keyword in _UPSTREAM_KEYWORDS):
        return "upstream"
    if any(keyword in sub_industry for keyword in _MIDSTREAM_KEYWORDS):
        return "midstream"
    return "midstream"


__all__ = ["Tier", "classify_tier"]
