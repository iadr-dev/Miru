---
name: live-snapshot-check
version: 1.1.0
description: One bounded, delayed live-ish quote check — TW best-5 snapshot, US symbol tick, Sponsor tick/futures snapshot — never a poll or a stream.
triggers: [即時, 現價, 最新報價, 五檔, 盤中, 美股即時, 期貨即時, 期貨報價, 逐筆快照, live quote, snapshot, best bid ask, futures snapshot, tick snapshot]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: true
tools: [get_canonical_context, get_source_health, get_live_quote_snapshot, run_bounded_observation, get_live_price_quote, get_tick_snapshot, get_futures_snapshot]
output_structure:
  - '五檔快照: 最新成交/開高低/量 + best bid/ask'
  - '美股即時報價: 單檔 US 股票/ETF 的 WebSocket 最新成交 tick，標明 tick 時間'
  - '盤中 tick / 期貨快照 (Sponsor): 單次價量與最佳買賣價，標明為單次有界觀測'
  - '通用有界觀測: 若五檔快照不可用時的替代能力摘要 (redacted)'
  - '有界性提醒: 這是單次觀測，不是串流或輪詢'
freshness_rule: 每次回覆須標明觀測時間點 (observed_at) 與"延遲、非即時"的性質；絕不暗示為即時串流。
boundaries:
  - 僅回報單次快照觀測結果，不建立輪詢或串流；每次呼叫都是一次獨立、有界的查詢。
  - get_tick_snapshot 與 get_futures_snapshot 為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明；兩者與 get_live_price_quote 一樣，都只是單次觀測，不得重複呼叫形成輪詢。
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
4. Pick the tool by instrument: a US stock/ETF → `get_live_price_quote(id, market)` (the latest
   WebSocket-streamed tick; report its tick time); a TW stock/ETF/index code when the caller wants
   tick-level price/volume/bid-ask → `get_tick_snapshot(id, market)` (Sponsor); a futures contract
   code → `get_futures_snapshot(id, market)` (Sponsor). If unentitled, report around the masked
   section. Each is one explicit check, never polled.
5. Every response makes explicit that this is **one bounded observation**, not a stream —
   repeat calls each count as a new explicit check, never an automatic refresh/poll.
