---
name: block-trade-and-broker-flow
version: 1.0.0
description: 鉅額交易 (block-trade) + 分點進出彙總 + 八大官股買賣 for one stock — broker/branch-level flow context beyond the base chip-flow-review skill.
triggers: [鉅額交易, 分點彙總, 八大官股, 官股銀行, block trade, broker aggregate flow, government bank]
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
tools: [get_canonical_context, get_source_health, get_block_trade, get_block_trade_broker_report, get_chip_broker_agg, get_government_bank_flows, get_securities_trader_info]
output_structure:
  - '鉅額交易執行紀錄: 區間內成交筆數與量價'
  - '分點鉅額交易明細: 券商別買賣量價 (Sponsor)'
  - '分點進出彙總: 每券商買賣量/價彙總 (Sponsor，與 get_chip_flows 的逐筆分點不同)'
  - '八大官股買賣: 區間內八大行庫買賣超 (Sponsor)'
  - '券商基本資料: 若回覆中提及券商代碼，附上名稱對照 (參考用)'
freshness_rule: 每一筆數字須標明交易日；四個 Sponsor 工具中任一遭遮蔽時，於對應區塊誠實說明，不得視為「無資料」。
boundaries:
  - 本技能的分點彙總 (get_chip_broker_agg) 為每券商彙總量價，與 chip-flow-review 技能使用的逐筆分點資料 (get_chip_flows) 是不同資料集，不得混用或互相取代。
  - 鉅額交易/分點/官股資料僅為籌碼面描述，不構成主力進出方向的買賣建議。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Block-trade and broker flow

## Procedure

1. Call `get_canonical_context(stock_id, market)` for a currentness baseline, then
   `get_source_health(stock_id, market)` — surface any stale/unreachable source first.
2. Call `get_block_trade(stock_id, market, start_date, end_date)` for the plain 鉅額交易
   execution rows, then `get_block_trade_broker_report(stock_id, market, start_date, end_date)`
   for the broker-level breakdown of the same event type (both Sponsor-tier).
3. Call `get_chip_broker_agg(stock_id, market, start_date, end_date)` (Sponsor-tier) for
   per-broker aggregated 分點 buy/sell volume/price — explicitly note this is a distinct
   dataset from `get_chip_flows`'s per-branch rows (used by the `chip-flow-review` skill), not
   an interchangeable view.
4. Call `get_government_bank_flows(stock_id, market, start_date, end_date)` (Sponsor-tier) for
   八大行庫 buy/sell rows.
5. If the response will cite a broker code, call `get_securities_trader_info(market)` once for
   the whole-market broker directory reference table and resolve the code to a name — this
   call is market-level only, never a per-symbol lookup.
6. Synthesize using `output_structure`, in order. For every Sponsor-tier section the caller is
   unentitled to, report around the masked section rather than silently dropping it.
