---
name: daily-context-brief
version: 1.0.0
description: Assemble a conversation-ready canonical brief (M5F context + Mode-C conversation package + source health) for one or more watchlist symbols.
triggers: [每日簡報, 觀察清單, 盤前, 盤後, daily brief, watchlist, conversation package]
design_pattern: pipeline
inputs:
  stock_ids:
    type: array
    required: true
tools: [get_canonical_context, build_conversation_package, get_source_health, get_price_quote]
output_structure:
  - '逐一個股: 用一句話帶出最新可用收盤價與當日漲跌 (get_price_quote)，不重建完整 OHLCV 表格 —
    該工具呼叫已自動附上報價卡片顯示完整開高低收與成交量，文字重複整張表格對讀者是冗餘資訊。與目前可用資料能力清單
    (payload_keys, 非原始資料)'
  - '對話摘要包: 每個符號的 ConversationFact 清單，含資料層級/更新時間/是否可用 (ConversationFact.source_id 為內部實作識別碼，絕不在輸出文字中提及)'
  - '來源健康度: 每個符號目前是否有來源異常或延遲'
  - '整體提醒: 每個符號皆須附上 currentness_caveat / non_trading_notice'
freshness_rule: 每個符號的每一筆記錄皆須附上 fetched_at 與 currentness_caveat；絕不省略任一符號的健康度檢查。
boundaries:
  - 本技能一次僅組裝已核准的三個核心工具的輸出，不額外呼叫任何其他工具去補充內容 — 需要更深入的分析請改用對應的專項技能 (如 chip-flow-review、earnings-quality)。
  - 逐一個股皆重複执行完整三步驟，不得為求簡潔省略任何一個符號的健康度或對話摘要包。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Daily context brief

## Procedure

1. For each `stock_id` in `stock_ids`, call `get_canonical_context(stock_id, market)` — the
   redacted M5F summary (per-capability provenance + `payload_keys`, never a raw payload) —
   and `get_price_quote(stock_id, market)` for the latest available EOD price bar. That tool
   call already renders its own price-quote card with the full OHLCV breakdown — mention only
   the closing price and day-over-day change in prose (one sentence), never restate open/high/
   low/volume/turnover in a markdown table; that duplicates the card with no added value.
2. For each `stock_id`, call `build_conversation_package(stock_id, market)` — the Mode-C
   `ConversationFact` list (already payload-free by construction): capability, source_id,
   data_class, timing_class, fetched_at, currentness_caveat, headline, is_available.
3. For each `stock_id`, call `get_source_health(stock_id, market)` and flag any symbol whose
   sources are degraded/stale before presenting its brief.
4. Assemble one section per symbol using `output_structure`, in order, so the whole watchlist
   reads as a scannable daily brief. Never merge multiple symbols' numbers into one aggregate
   figure — each symbol's brief stands on its own, tagged with its own caveats.
5. This skill deliberately calls no other tool — if the caller wants a deeper read on any one
   symbol (chips, valuation, earnings, etc.), name the appropriate specialist skill rather than
   reaching outside this skill's allowlist.
