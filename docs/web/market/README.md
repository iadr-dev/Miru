# 大盤 views — folder index

Per-view design docs for the Market dashboard. **Full page overview** (two-row tab nav, 每日焦點
layout ASCII, governance): [../market.md](../market.md). Shared language:
[../design-system.md](../design-system.md) · [../interaction-spec.md](../interaction-spec.md).

Nav = **two rows**: Row-1 **market tabs** (config-driven from `MarketProfile` — 台股/美股/ETF, +JP/GB
later with zero UI rewrite) · Row-2 **view sub-tabs** (per-market; TW shown below). Every doc has a
**Visual design** section + maps to a real endpoint.

## TW view sub-tabs

| View | Doc | Headline content |
|------|-----|------------------|
| 每日焦點 (default) | [每日焦點.md](每日焦點.md) | index cards · intraday · breadth · 貢獻排行 · Top-N · 熱門股 · 本週行事曆 · macro strip |
| 熱力圖 | [熱力圖.md](熱力圖.md) | 類股 treemap · 產業別漲跌 · 個股漲跌分佈 |
| 籌碼 | [籌碼.md](籌碼.md) | 三大法人 · 法人排行 · 融資券/維持率 · 外資/主力/官股🔒 |
| 產業 | [產業.md](產業.md) | 產業鏈資金流🔒 · 類股輪動 · 主題總覽 |
| 新聞 | [新聞.md](新聞.md) | 市場新聞 (即時/財經) · 名人事件 |
| 處置 | [處置.md](處置.md) | 處置王 card-grid (風險/處置中/即將出關/自結發布) |
| 名人 | [名人.md](名人.md) | 名人事件 feed |

## Row-1 market sections
| Section | Doc | Notes |
|---------|-----|-------|
| 美股 | [美股.md](美股.md) | US indices/movers/economic — phased (module 19); ⚑ per-market color decision |
| ETF | [ETF.md](ETF.md) | 主動ETF 追蹤/操作日報🔒 · 折溢價 · 受益人數/AUM |

All views: 紅漲綠跌+▲▼, tabular-nums, non-trading `<Caveat>`, sponsor→馬賽克, dark-first, `ChartWithDataSheet` (§7.11).
