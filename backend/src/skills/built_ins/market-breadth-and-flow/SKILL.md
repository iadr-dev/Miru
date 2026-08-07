---
name: market-breadth-and-flow
version: 1.0.0
description: 全市場層級 TAIEX/TPEx breadth structure — 5-second index/order-book snapshots, whole-market 三大法人 totals, 報酬指數, 非TW指數快照, and the US/GB/JP/EU equity/ETF screener.
triggers: [大盤, 加權指數, 櫃買指數, 5秒指數, 報酬指數, 全市場法人, equity screener, market breadth]
design_pattern: tool_wrapper
inputs:
  market:
    type: string
    required: true
  trade_date:
    type: string
    required: false
tools: [get_taiex_index_snapshot, get_order_book_stats, get_total_return_index, get_index_snapshots, get_institutional_flow_total_market, get_institutional_flow_wide, run_equity_screener_us]
output_structure:
  - 'TAIEX 5秒指數讀值: 當日走勢'
  - '全市場委託/成交統計: 5秒委託簿彙總'
  - '報酬指數: TAIEX/TPEx 報酬指數序列'
  - '非TAIEX指數5秒快照: 其他指數 (Sponsor)'
  - '全市場三大法人買賣總額: 依類別彙總'
  - '個股三大法人寬表 (若查詢者提供個股代碼): 每日各類別買賣欄位並列'
  - '美股/歐股/日股篩選結果: 若查詢者要求跨市場條件篩選 (US/GB/JP/EU)'
freshness_rule: 每一子面板須標明其交易日；本技能不提供漲跌家數/指數貢獻排行 (無對應工具)，須在缺漏欄位處誠實說明。
boundaries:
  - 本技能沒有「漲跌分佈/家數」與「指數貢獻排行」欄位 — 現有工具僅能提供指數讀值本身，不得用其他數字替代或估算這兩項缺漏欄位。
  - get_index_snapshots 為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明。
  - run_equity_screener_us 僅服務 US/GB/JP/EU 市場，且僅回傳符合條件的清單，不做任何買進/賣出建議。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Market breadth and flow (narrow, honest version)

## Procedure

1. Call `get_taiex_index_snapshot(market="TW", trade_date)` for the whole-market TAIEX 5-second
   index reading, and `get_order_book_stats(market="TW", trade_date)` for the matching
   whole-exchange order-book/trade aggregate.
2. Call `get_total_return_index(id, market="TW", start_date, end_date)` with `id` set to the
   pseudo-code `TAIEX` or `TPEx` (never a real stock code) for the 報酬指數 series.
3. If the caller wants non-TAIEX index readings, call `get_index_snapshots(market="TW",
   trade_date)` (Sponsor-tier) — no `data_id` filter is documented upstream, so the full set of
   index readings comes back; if unentitled, report around the masked section.
4. Call `get_institutional_flow_total_market(market)` for the whole-market three-major-
   institutional-investor buy/sell total by category. If the caller names a specific stock,
   additionally call `get_institutional_flow_wide(stock_id, market, start_date, end_date)` for
   the same stock's per-day, per-category wide-format view.
5. If the caller wants a cross-market equity/ETF/fund screen (US/GB/JP/EU only), call
   `run_equity_screener_us(query)` with the caller's AND/OR field conditions.
6. Synthesize using `output_structure`, in order. Explicitly state that 漲跌分佈/家數 and 指數
   貢獻排行 breadth/contribution breakdowns are not available — only the plain index level is.
