---
name: valuation-band
version: 1.0.0
description: PE/PB/dividend-yield band over a window for one stock, plus its announced dividend schedule for yield context.
triggers: [估值, PE, PB, 本益比, 淨值比, 殖利率, valuation, PE ratio, PB ratio, dividend yield]
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
tools: [get_canonical_context, get_source_health, get_valuation_ratios, get_dividend_schedule]
output_structure:
  - 'PE/PB 區間: 窗口內最高/最低/目前值與相對位置的描述'
  - '殖利率走勢: dividend_yield 欄位的區間變化'
  - '股利時程脈絡: 近期已宣告股利事件，供殖利率解讀參考'
  - '歷史帶位描述: 目前值相對於窗口內區間的敘述性定位 (非目標價)'
freshness_rule: 每個 PE/PB/殖利率數字須標明 trade_date；股利事件須標明宣告日期；缺一律標明資料截止時間。
boundaries:
  - 僅描述目前估值相對於查詢窗口內歷史區間的位置，不提供目標價、合理價或買賣建議。
  - 窗口長度由呼叫者指定；若未指定，明確說明使用的預設區間，不得隱藏假設。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Valuation band

## Procedure

1. Call `get_canonical_context(stock_id, market)` for a currentness baseline, then
   `get_source_health(stock_id, market)` — surface any stale/unreachable source first. Its
   `built_at` timestamp is the anchor for "today" in step 2 below — never substitute your own
   notion of the current date, which can silently drift from wall-clock time.
2. Call `get_valuation_ratios(stock_id, market, start_date, end_date)` for daily PER/PBR/
   dividend-yield rows over the window (default to a reasonable window, e.g. 1 year, ending at
   `built_at`'s date, if the caller gives no dates — state the default explicitly, including the
   actual end date used). Compute the window's min/max/current for PER and PBR from the returned
   rows; do not assume a pre-computed band field exists.
3. Call `get_dividend_schedule(stock_id, market)` for the announced dividend/ex-dividend
   timeline — context for interpreting the yield series, not a separate yield figure.
4. Synthesize using `output_structure`, in order. Describe "目前 PE 落在窗口內第 X 百分位/接近
   高點或低點" as a purely descriptive historical positioning — never as a signal to buy the
   dip or take profit, and never as a fair-value/target-price estimate.
