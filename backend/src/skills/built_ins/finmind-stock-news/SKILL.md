---
name: finmind-stock-news
version: 1.0.0
description: FinMind's own news dataset for one stock, one trading day at a time — distinct from the RSS/Finnhub news aggregator (get_news_feed) and PTT buzz tool (get_ptt_board).
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
  - '新聞列表: 指定交易日的新聞條目 (標題/來源/連結)'
  - '引用提醒: 每則新聞皆為第三方內容，需標明來源'
freshness_rule: get_finmind_stock_news 一次僅服務一個交易日；每則新聞須標明其發布日期。
boundaries:
  - 這不是一個多來源新聞聚合器 — 沒有 category 篩選、沒有 RSS/Finnhub 彙整。多來源即時新聞請改用 get_news_feed；PTT 聲量資料請改用 get_ptt_board。
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
