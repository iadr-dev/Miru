---
name: sector-rotation
version: 1.0.0
description: 類股輪動與熱力圖 — 各產業漲跌熱力圖、單一產業的成分股表現、各產業法人買賣超排名輪動、與 TAIEX 相對的週線相對輪動圖 (RRG 四象限)。描述性資料，不含買賣建議。
triggers: [類股, 類股輪動, 族群輪動, 產業熱力圖, 類股熱力圖, 哪個類股強, 哪個族群, 類股漲跌, 產業成分股, 相對輪動, RRG, sector rotation, sector heatmap, industry heatmap, relative rotation]
design_pattern: tool_wrapper
inputs:
  market:
    type: string
    required: false
  trade_date:
    type: string
    required: false
  industry:
    type: string
    required: false
tools: [get_sector_heatmap, get_sector_constituents, get_sector_rotation, get_sector_rotation_rrg]
output_structure:
  - '類股熱力圖: 各產業在所選期間的平均漲跌幅、成分股檔數與成交值／市值，標明交易日與期間'
  - '產業成分股: 單一產業內個股的漲跌幅、收盤、成交量與成交值排行'
  - '法人輪動排名: 各產業在近 10 個交易日依法人買賣超的逐日名次變化'
  - '相對輪動圖 (RRG): 各產業指數相對 TAIEX 的 RS-Ratio／RS-Momentum 所在象限 (領先／轉弱／落後／改善) 與近期軌跡'
  - '資料時效與限制: 交易日、期間、權重方式與缺料說明'
freshness_rule: 四個工具皆讀取已入庫的收盤後整市場資料（RRG 為週線），不是盤中即時；每個面板須標明其交易日或結束日，找不到該日資料時如實說明並改用最近可用日，不得假裝是今天。
boundaries:
  - 熱力圖、輪動名次與 RRG 象限只描述各產業目前相對位置與近期軌跡，不是買賣訊號；不得說「該買進領先象限」或預測象限接下來的移動。
  - 法人輪動排名依法人買賣超（買進減賣出）排序，只描述已發生的資金流向，不推論法人動機。
  - 熱力圖的產業平均漲跌幅可能以成交值加權，須依工具回傳說明加權方式，不要與簡單平均混用。
  - RRG 使用 TWSE 公布的產業分類指數對 TAIEX 計算；缺少某產業指數時照實說明。
  - 產業成分股清單需要先有產業名稱；產業名稱以熱力圖回傳的名稱為準，不憑記憶拼寫。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Sector rotation and heatmap (descriptive, non-trading)

## Procedure

1. For "which sectors are strong/weak" call `get_sector_heatmap(market, trade_date, period,
   size_by)`. `period` runs from immediate (the day) to a year; `size_by` picks turnover, market
   value or volume for the tile size. Report the industries with the largest moves in both
   directions, the period and the trading day.
2. To look inside one industry, call `get_sector_constituents(market, industry, trade_date)` with
   the exact industry name the heatmap returned. Omitting `industry` ranks every symbol together;
   use that only when the caller asks for the whole-market ranking.
3. For "which sectors are institutions rotating into", call `get_sector_rotation(market,
   end_date)` — each industry's daily rank by net institutional flow over the last 10 trading
   days. Describe the rank changes without inferring motive.
4. For a relative-strength view, call `get_sector_rotation_rrg(market, end_date)`: the weekly
   RRG (RS-Ratio and RS-Momentum against TAIEX) with each industry's quadrant and trail. Explain
   the four quadrants in one sentence and describe where industries sit now.
5. If a tool returns nothing for the requested date, relay its message and the date that was
   used instead. Do not fill gaps from memory.
