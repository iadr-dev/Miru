---
name: intraday-and-multi-timeframe-price
version: 1.0.0
description: Tick-level trade history, minute K-bars, weekly/monthly bars, ten-year price line, and 暫停交易 announcements for one stock (all Sponsor-tier).
triggers: [tick, 分鐘K, 週K, 月K, 十年價線, 暫停交易, intraday, minute bar, ten-year price]
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
tools: [get_canonical_context, get_source_health, get_price_tick, get_intraday_kbar, get_price_multi_tf, get_ten_year_price, get_trading_halts]
output_structure:
  - '逐筆成交紀錄: 單一交易日內的 tick 資料'
  - '分鐘K棒: 單一交易日內的分鐘線'
  - '週線/月線: 區間內的週K/月K'
  - '十年價線: 長期收盤價序列'
  - '暫停交易公告: 若有'
freshness_rule: 逐筆與分鐘K棒僅能一次查詢一個交易日 (上游限制)；每個子區塊須標明其實際資料日期或期間。
boundaries:
  - get_price_tick 與 get_intraday_kbar 上游一次僅支援一個交易日，若呼叫者要求的區間跨多日，須分次查詢並在回覆中說明，不得暗示已涵蓋整個區間。
  - 五個工具皆為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明。
  - 本技能僅描述價格/成交結構，不提供任何技術指標判讀、支撐壓力或買賣時點建議 (技術面判讀屬於尚未建置的 technical-read 技能，依賴 analytics 層)。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Intraday and multi-timeframe price

## Procedure

1. Call `get_canonical_context(stock_id, market)` for a currentness baseline, then
   `get_source_health(stock_id, market)` — surface any stale/unreachable source first.
2. If the caller wants tick-level detail for one specific trading day, call
   `get_price_tick(stock_id, market, start_date, end_date)` — note the upstream limit of one
   trading day per request; if the caller's range spans multiple days, say so and query one day
   at a time rather than silently truncating.
3. Similarly, call `get_intraday_kbar(stock_id, market, start_date, end_date)` for minute-bar
   series, one trading day at a time.
4. Call `get_price_multi_tf(stock_id, market, start_date, end_date)` for weekly + monthly bars
   in one bundled report, and `get_ten_year_price(stock_id, market, start_date, end_date)` for
   the long-run close-price series.
5. Call `get_trading_halts(stock_id, market, start_date, end_date)` for any 暫停交易
   announcement in the window; if none, say so explicitly.
6. Synthesize using `output_structure`, in order. This skill reports price/volume structure
   only — it does not compute or discuss moving averages, momentum, or any other technical
   indicator (that analysis depends on the not-yet-built `analytics/` indicators layer and is
   out of scope here).
