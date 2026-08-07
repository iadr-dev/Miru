---
name: corporate-actions-and-market-value
version: 1.0.0
description: 減資/下市/分割/面額變動 corporate-action reference events, daily market value + market-cap-weight ranking, 當沖 eligibility, and 漲跌停參考價 for one stock.
triggers: [減資, 下市, 分割, 面額變動, 市值, 市值比重, 當沖, 漲跌停, corporate action, market cap, market value, day trading]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: true
  start_date:
    type: string
    required: false
  end_date:
    type: string
    required: false
tools: [get_canonical_context, get_source_health, get_corporate_action_events, get_market_value, get_market_value_weight, get_day_trading, get_price_limit]
output_structure:
  - '公司行動事件: 減資恢復買賣參考價/下市櫃/分割反分割/變更面額 (若有)'
  - '市值走勢: 區間內每日市值 (Sponsor)'
  - '市值比重排行: 區間內每日全市場排名 (Sponsor)'
  - '當沖標的與量值: 是否列入當沖標的與當日量值'
  - '漲跌停參考價: 區間內每日參考/漲停/跌停價'
freshness_rule: 每個子區塊須標明其資料日期；公司行動事件本質上是事件驅動且各自可能為空，缺一律說明「查詢區間內無此類事件」而非留白。
boundaries:
  - get_market_value 與 get_market_value_weight 為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明。
  - 減資/下市/分割等公司行動事件僅為歷史事件描述，不對股價後續影響做任何推測或建議。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Corporate actions and market value

## Procedure

1. Call `get_canonical_context(stock_id, market)` for a currentness baseline, then
   `get_source_health(stock_id, market)` — surface any stale/unreachable source first.
2. Call `get_corporate_action_events(stock_id, market, start_date, end_date)` — one bundled
   report covering capital-reduction reference price, delisting, split/reverse-split, and
   par-value-change events. Each sub-list may be independently empty; state that plainly rather
   than implying a section with no rows means an error.
3. Call `get_market_value(stock_id, market, start_date, end_date)` (Sponsor-tier) for daily
   market-capitalization rows, and `get_market_value_weight(stock_id, market, start_date,
   end_date)` (Sponsor-tier) for the daily whole-market market-cap-weight ranking. If
   unentitled, report around the masked sections.
4. Call `get_day_trading(stock_id, market, start_date, end_date)` for 當沖 eligibility marker +
   volume/value rows, and `get_price_limit(stock_id, market, start_date, end_date)` for daily
   reference/limit-up/limit-down price rows.
5. Synthesize using `output_structure`, in order, every figure tagged with its trading date.
