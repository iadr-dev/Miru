---
name: macro-dashboard
version: 1.0.0
description: 匯率/利率/黃金/原油/美債殖利率/Fear-Greed macro context plus TW 景氣對策信號 (藍黃紅燈) — market-level, no per-symbol scope.
triggers: [匯率, 利率, 黃金, 原油, 美債殖利率, 景氣對策信號, 藍燈, 紅燈, macro, fear and greed, business indicator]
design_pattern: tool_wrapper
inputs:
  start_date:
    type: string
    required: false
  end_date:
    type: string
    required: false
tools: [get_source_health, get_macro_indicators, get_business_indicator]
output_structure:
  - '匯率: 區間走勢 (TW 掛牌匯率)'
  - '主要央行利率: 區間走勢 (全球)'
  - '黃金/原油: 區間價格走勢 (全球)'
  - '美債殖利率: 區間走勢 (US)'
  - 'Fear & Greed Index: 區間讀值 (US, Sponsor 子欄位)'
  - '景氣對策信號: 台灣月度藍黃紅燈讀值 (Sponsor)'
freshness_rule: 每一子面板須標明其實際資料期間；六個子資料集分屬 TW/GLOBAL/US 不同市場範疇，須各自標明其所屬市場，不得混稱為單一市場資料。
boundaries:
  - get_macro_indicators 整包為 Sponsor 分級 (即使多數子欄位單獨來看屬 public)，未取得授權時整份報告的遮蔽區塊須誠實說明。
  - 本技能為全球/台灣宏觀市場層級資料，不對應任一單一個股，也不提供任何資產配置或進出場時點建議。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Macro dashboard

## Procedure

1. Call `get_source_health(market)` (any representative market id, e.g. `TW`) as a currentness
   baseline — this domain has no per-symbol canonical-context wrapper, every dataset here is
   market-level.
2. Call `get_macro_indicators(start_date, end_date)` (Sponsor-tier overall) — one bundled
   report covering exchange rate (TW-scoped), central-bank policy rate / gold / crude oil
   (GLOBAL-scoped), and US Treasury yield / Fear & Greed Index (US-scoped). Report each
   sub-panel tagged with the market it actually belongs to — never imply all six panels share
   one market scope. `health_status` is the worst status among the bundled sources: when it is
   `unreachable` (or any non-`ok` tier), an empty sub-panel means that source could not be
   reached, not that there is no reading - say so, never "no data".
3. Call `get_business_indicator(market="TW", start_date, end_date)` (Sponsor-tier) for
   Taiwan's monthly 景氣對策信號 (藍黃紅燈) readings. Apply the same `health_status` rule: an
   empty result with `unreachable` is an outage, not a missing signal.
4. If the caller is unentitled, report around the masked sections rather than omitting the
   dashboard entirely.
5. Synthesize using `output_structure`, in order. Never translate a macro reading into an
   asset-allocation recommendation, timing call, or currency/commodity trade idea.
