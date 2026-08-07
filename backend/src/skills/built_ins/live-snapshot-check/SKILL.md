---
name: live-snapshot-check
version: 1.0.0
description: One bounded, delayed live-ish quote check for a TW symbol — never a poll or a stream.
triggers: [即時, 現價, 最新報價, 五檔, 盤中, live quote, snapshot, best bid ask]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: true
tools: [get_canonical_context, get_source_health, get_live_quote_snapshot, run_bounded_observation]
output_structure:
  - '五檔快照: 最新成交/開高低/量 + best bid/ask (TWSE MIS)'
  - '通用有界觀測: 若五檔快照不可用時的替代能力摘要 (redacted)'
  - '有界性提醒: 這是單次觀測，不是串流或輪詢'
freshness_rule: 每次回覆須標明觀測時間點 (observed_at) 與"延遲、非即時"的性質；絕不暗示為即時串流。
boundaries:
  - 僅回報單次快照觀測結果，不建立輪詢或串流；每次呼叫都是一次獨立、有界的查詢。
  - 五檔快照為延遲資料，不得用於下單或極短線判斷；不提供任何買賣時點建議。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Live snapshot check

## Procedure

1. Call `get_canonical_context(stock_id, market)` for a currentness baseline, then
   `get_source_health(stock_id, market)` — if the live-ish source is flagged stale or
   unreachable, say so before presenting the snapshot.
2. Call `get_live_quote_snapshot(stock_id, market)` — a single, purpose-built TWSE MIS best-5
   bid/ask + last-trade observation for one TW symbol. Report `observed_at`, `last_price`,
   `open`/`high`/`low`, `volume`, and `best_bid`/`best_ask` verbatim; if `snapshot` is `None`,
   say the source returned nothing rather than inventing values.
3. If the caller asks about a capability other than the TWSE MIS quote (e.g. a different
   live-ish capability the router serves), call `run_bounded_observation(stock_id, market,
   capability)` instead — it returns the generic, redacted `CapabilitySummary` shape (never a
   raw payload). Use this as the fallback path, not the default.
4. Every response makes explicit that this is **one bounded observation**, not a stream —
   repeat calls each count as a new explicit check, never an automatic refresh/poll.
