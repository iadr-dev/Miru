---
name: finmind-stock-news
version: 1.0.0
description: FinMind's own news dataset for one stock, one trading day at a time — distinct from the excluded RSS/GDELT/Finnhub news aggregator and PTT buzz tools (neither of which has a tool in this registry).
triggers: [finmind新聞, 個股新聞, stock news, finmind news]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: true
  trade_date:
    type: string
    required: false
tools: [get_source_health, get_finmind_stock_news]
output_structure:
  - '新聞列表: 指定交易日的 FinMind 新聞條目 (標題/來源/連結)'
  - '引用提醒: 每則新聞皆為第三方內容，需標明來源'
freshness_rule: get_finmind_stock_news 一次僅服務一個交易日；每則新聞須標明其發布日期。
boundaries:
  - 這不是一個多來源新聞聚合器 — 沒有 category 篩選、沒有 RSS/GDELT/Finnhub 彙整，也沒有 PTT 聲量資料，這些皆刻意排除在工具化範圍之外。
  - 每則新聞內容視為第三方資料，僅可引用/摘要並標明來源，不得改寫為本平台的分析結論或投資判斷。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# FinMind stock news

## Procedure

1. Call `get_source_health(stock_id, market)` as a currentness baseline.
2. Call `get_finmind_stock_news(stock_id, market, trade_date)` — FinMind's own news dataset,
   one trading day per request. If the caller wants a range, call this once per day of
   interest and say so explicitly rather than implying one call covers a range.
3. Present each row with its title/source/link, tagged with its publish date. Every claim
   drawn from a news row is attributed to that row's source — never restated as first-party
   analysis or an investment view.
