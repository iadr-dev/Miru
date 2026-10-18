---
name: market-breadth-and-flow
version: 1.1.0
description: 全市場層級 TAIEX/TPEx breadth structure — 漲跌家數分佈, 指數貢獻估算, TW/US 指數跑馬燈 (指數/匯率/原油/殖利率), 5-second index/order-book snapshots, whole-market 三大法人 totals, 報酬指數, 非TW指數快照, and the US/GB/JP/EU equity/ETF screener.
triggers: [大盤, 加權指數, 櫃買指數, 5秒指數, 報酬指數, 全市場法人, 漲跌家數, 漲跌分佈, 指數貢獻, 美股四大指數, 費半, 跑馬燈, equity screener, market breadth, advance decline, index contribution, market strip]
design_pattern: tool_wrapper
inputs:
  market:
    type: string
    required: true
  trade_date:
    type: string
    required: false
tools: [get_taiex_index_snapshot, get_order_book_stats, get_total_return_index, get_index_snapshots, get_institutional_flow_total_market, get_institutional_flow_wide, run_equity_screener_us, get_market_index_strip, get_us_market_index_strip, get_advance_decline, get_index_contribution]
output_structure:
  - '指數跑馬燈: TW (加權/櫃買/匯率/黃金/原油) 或 US (S&P500/道瓊/那斯達克/費半/VIX/美10年債殖利率/Fed 利率) 的現值與較前一筆變動，各項獨立標明讀值時間'
  - '漲跌家數分佈: 單日上漲/下跌/平盤家數與漲跌幅區間分佈 (get_advance_decline)'
  - '指數貢獻估算: 市值權重 x 漲跌幅的近似貢獻排行，須附 contribution_caveat 標明非官方數字 (Sponsor)'
  - 'TAIEX 5秒指數讀值: 當日走勢'
  - '全市場委託/成交統計: 5秒委託簿彙總'
  - '報酬指數: TAIEX/TPEx 報酬指數序列'
  - '非TAIEX指數5秒快照: 其他指數 (Sponsor)'
  - '全市場三大法人買賣總額: 依類別彙總'
  - '個股三大法人寬表 (若查詢者提供個股代碼): 每日各類別買賣欄位並列'
  - '美股/歐股/日股篩選結果: 若查詢者要求跨市場條件篩選 (US/GB/JP/EU)'
freshness_rule: 每一子面板須標明其交易日；跑馬燈各項是獨立讀值，個別標明時間，缺少的項目照實說明而非以他項代替；漲跌家數與指數貢獻為收盤後批次資料，不是盤中即時。
boundaries:
  - 漲跌家數與指數貢獻分別來自 get_advance_decline 與 get_index_contribution；兩者缺料時照實說明，不得用其他數字替代或自行估算。
  - get_index_contribution 是「市值權重 x 漲跌幅」的近似估算，並非交易所公布的流通調整貢獻點數，必須附上 contribution_caveat，且為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明。
  - 指數跑馬燈與漲跌分佈僅描述當下市況，不推論後市，不構成進出場建議。
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
6. For a quick "how is the market today" question, call `get_market_index_strip()` (TW tab) and/or
   `get_us_market_index_strip()` (US tab) — each item is read independently, so report the ones
   that are missing as missing. For breadth, call `get_advance_decline(market, trade_date)`
   (advancing/declining/unchanged counts bucketed by % change), and for what moved the index,
   `get_index_contribution(market, trade_date, top_n)` (Sponsor-tier, APPROXIMATE — always carry
   its `contribution_caveat`; if unentitled, report around the masked section).
7. Synthesize using `output_structure`, in order, with every panel tagged by its trading day.
