---
name: margin-and-short-interest
version: 1.0.0
description: 融資融券/借券/融資維持率/外資持股/股權分散 — per-stock margin, short-interest, and shareholding-concentration read (one stock, plus whole-market margin/short totals on request).
triggers: [融資, 融券, 借券, 融資維持率, 外資持股, 股權分散, 千張大戶, margin, short interest, securities lending]
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
  include_whole_market:
    type: boolean
    required: false
    default: false
tools: [get_canonical_context, get_source_health, get_margin_short, get_margin_short_total_market, get_securities_lending, get_margin_short_sale_suspension, get_daily_short_sale_balances, get_margin_maintenance, get_foreign_shareholding, get_shareholder_dispersion]
output_structure:
  - '融資融券餘額: 個股區間變化'
  - '借券成交: 個股借券成交/賣出趨勢'
  - '融資融券/借券暫停公告: 若有'
  - '信用交易配額餘額: 融資融券+借券的每日配額/餘額'
  - '外資持股比率: 個股區間變化'
  - '股東集中度: 千張大戶比率/股東人數 (Sponsor)'
  - '大盤融資維持率: 僅在 include_whole_market=true 時提供 (Sponsor)'
freshness_rule: 每一筆數字須標明交易日；股權分散與大盤融資維持率為 Sponsor 資料，缺一律標明資料截止時間。
boundaries:
  - 融資融券/借券餘額變化僅為籌碼面描述，不構成軋空、斷頭或任何方向性判斷的依據。
  - 股權分散 (get_shareholder_dispersion) 與大盤融資維持率 (get_margin_maintenance) 為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明。
  - 不提供全市場逐股掃描；whole-market 工具僅回傳單一市場層級彙總數字，never a per-symbol fan-out。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Margin and short-interest read

## Procedure

1. Call `get_canonical_context(stock_id, market)` for a currentness baseline, then
   `get_source_health(stock_id, market)` — surface any stale/unreachable source first.
2. Call `get_margin_short(stock_id, market, start_date, end_date)` for daily 融資融券 balance
   rows, and `get_securities_lending(stock_id, market, start_date, end_date)` for 借券成交 rows
   over the same window.
3. Call `get_margin_short_sale_suspension(stock_id, market, start_date, end_date)` — report any
   暫停融券賣出 announcement in the window; if none, say so explicitly.
4. Call `get_daily_short_sale_balances(stock_id, market, start_date, end_date)` for the credit-
   limit short-sale quota/balance rows (margin + securities-lending combined view).
5. Call `get_foreign_shareholding(stock_id, market, start_date, end_date)` for the foreign-
   shareholding-limit/ratio trend, and `get_shareholder_dispersion(stock_id, market, start_date,
   end_date)` (Sponsor-tier) for the 股權分散/千張大戶 concentration signal — if unentitled,
   report around the masked section.
6. Only if `include_whole_market` is true, additionally call
   `get_margin_short_total_market(market)` and `get_margin_maintenance(market)`
   (Sponsor-tier) for the whole-market category total and maintenance-ratio reading — always a
   market-level aggregate, never a per-symbol fan-out over the whole market.
7. Synthesize using `output_structure`, in order, every figure tagged with its trading date.
