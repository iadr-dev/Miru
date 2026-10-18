---
name: technical-trend-read
version: 1.0.0
description: 技術面與趨勢讀數 — 單一個股／ETF 的 MA、布林通道、KD、MACD 指標序列、支撐／壓力位與 K 線型態，以及六維度趨勢評分 (0-100)。純描述圖表與資料，絕非買賣訊號。
triggers: [技術面, 技術分析, 均線, 布林通道, KD, MACD, 支撐, 壓力, 支撐壓力, K線型態, 黃金交叉, 死亡交叉, 趨勢評分, 趨勢強度, technical analysis, moving average, bollinger, macd, support resistance, candlestick pattern, trend score]
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
  days:
    type: integer
    required: false
tools: [get_canonical_context, get_technical_indicators, get_trend_score]
output_structure:
  - '指標讀數: MA／布林通道／KD／MACD 在所選期間的最新值與近期走向，標明期間與日期'
  - '支撐／壓力位: 工具算出的價位區與其依據，標示為圖表描述，不是進出場價'
  - 'K 線型態: 期間內命中的型態與日期，僅陳述型態名稱'
  - '趨勢評分: 0-100 的綜合分數與六個維度 (技術指標、法人籌碼、融資融券趨勢、分點集中度、類股輪動位置、與大盤連動) 的拆解，缺值維度須明確說明'
  - '近期評分走勢: 評分歷史，用來描述趨勢強度的變化'
freshness_rule: 指標與趨勢評分皆以已入庫的日資料（收盤後）計算，不是盤中即時；須標明資料截止日與所選期間，趨勢評分若為快取則標明計算日期，缺資料時照實說明。
boundaries:
  - 所有指標值、支撐／壓力、型態與趨勢評分都是對圖表與資料的描述，不是買賣、持有、停損或目標價建議；黃金交叉、超買超賣等字眼只能作為型態名稱敘述，不得推論後續漲跌。
  - 趨勢評分是描述性的「趨勢強度」讀數，不是預測，也不同於 stock-score 的多因子綜合評分；兩者不要混為一談。
  - 趨勢評分任一維度缺值時，須誠實說明該維度無法取得，不得以中性值補上。
  - 支撐／壓力是演算法由歷史價位整理出的參考區，不保證有效，不得說成「會在這裡反彈／回檔」。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Technical indicators and trend score (descriptive, non-trading)

## Procedure

1. Call `get_canonical_context(stock_id, market)` for a currentness baseline.
2. For indicator questions (均線、布林、KD、MACD、支撐壓力、K 線型態), call
   `get_technical_indicators(id, market, start_date, end_date)`. Choose a window that fits the
   question (default to the recent period if the caller gave none) and say which window was used.
   Describe the latest readings and how they moved; list pattern hits by name and date only.
3. For "how strong is the trend", call `get_trend_score(id, market, days)`. Present the 0-100
   score, then the six dimensions it combines; call out any dimension the tool reports as
   unavailable instead of smoothing it over. Use the trailing history only to describe how the
   trend strength changed.
4. Both results are descriptions of past data. Do not translate a reading into an instruction,
   and do not predict what price will do next.
5. If both tools are relevant, present indicators first and the trend score second, each tagged
   with its as-of date.
