---
name: industry-chain-money-flow
version: 1.0.0
description: 產業鏈資金流 whole-market money-flow aggregate for one trading day, plus a stock's static 產業鏈分類 rows.
triggers: [產業鏈, 產業鏈資金流, 產業分類, industry chain, industry money flow]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: false
  market:
    type: string
    required: true
  trade_date:
    type: string
    required: false
tools: [get_source_health, get_industry_money_flow, get_industry_chain_classification]
output_structure:
  - '產業鏈資金流: 單一交易日全市場彙總 (Sponsor)'
  - '個股產業鏈分類: 若呼叫者提供個股代碼，列出該股所屬產業鏈靜態分類 (Sponsor)'
freshness_rule: 產業鏈資金流僅為單一交易日彙總 (非區間)；分類為靜態/半靜態資料，仍須標明查詢當次的資料截止時間。
boundaries:
  - get_industry_money_flow 為全市場層級彙總，非逐股掃描結果的重新包裝；一次查詢僅回傳單一交易日的彙總，不得要求或暗示回傳整段區間。
  - 兩個工具皆為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明。
  - 僅描述資金流與分類，不對任一產業鏈或成分股做買賣建議。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Industry-chain money flow

## Procedure

1. Call `get_source_health(market)` as a currentness baseline — this domain has no per-symbol
   canonical-context wrapper.
2. Call `get_industry_money_flow(market, trade_date)` (Sponsor-tier) for the whole-market
   產業鏈資金流 aggregate for one trading day — a genuinely market-level read, not a per-symbol
   fan-out; if the caller wants a trend, this must be called once per day of interest rather
   than requested as a range (the tool itself is single-day).
3. If the caller names a specific stock, additionally call
   `get_industry_chain_classification(stock_id, market, start_date, end_date)` (Sponsor-tier)
   for the static industry-chain classification rows underlying the aggregate above.
4. If unentitled to either tool, report around the masked section.
5. Synthesize using `output_structure`, in order.
