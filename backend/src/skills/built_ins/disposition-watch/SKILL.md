---
name: disposition-watch
version: 1.1.0
description: 公布處置有價證券 status stream for one stock, plus the whole-market 處置/注意股 board (風險股/處置中/即將出關 with a rule-based, caveated projection).
triggers: [處置, 處置股, 注意股, 處置王, 即將出關, 風險股, 處置看板, disposition, disposition securities, disposition board]
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
tools: [get_canonical_context, get_source_health, get_disposition_securities, get_disposition_board]
output_structure:
  - '處置公告紀錄: 區間內處置有價證券公告事件列表'
  - '處置看板 (使用者問全市場或「哪些股票快進處置/快出關」時): 風險股/處置中/即將出關 三個分組，逐檔列價格、法人買賣、融資比與「距下次處置天數」規則推估'
  - '單股路徑尚無法提供的部分: 注意股狀態與推估 (單股公告事件流本身沒有，需改用處置看板；看板推估須附 projection_caveat)'
freshness_rule: 每筆處置公告須標明公告/處置起訖日期；若查詢區間內沒有公告，明確說明"此區間無處置公告"而非留白。
boundaries:
  - 單股公告事件流 (get_disposition_securities) 不含注意股狀態與進入處置推估；這些只存在於處置看板 (get_disposition_board)，不得用公告流臆測或外推。
  - 看板的「距下次處置天數」是依「滾動視窗內重複被列注意股」規則做的推估，並非交易所公布的數字，必須附上 projection_caveat，不得說成預測或保證，更不得推論股價走勢。
  - 僅描述已公告的處置事件，不對股價後續走勢做任何推測或建議。
  - get_disposition_securities 為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Disposition watch (narrow, honest version)

## Procedure

1. Call `get_canonical_context(stock_id, market)` for a currentness baseline, then
   `get_source_health(stock_id, market)` — surface any stale/unreachable source first.
2. Call `get_disposition_securities(stock_id, market, start_date, end_date)` (Sponsor-tier) —
   the FinMind 公布處置有價證券 event stream. This is narrower than a full 處置狀態 composite:
   it is the announcement stream itself, not a live "currently under disposition" flag or a
   forward projection. If the caller is unentitled, report around the masked section.
3. For a whole-market question ("哪些股票快進處置", "處置王", "即將出關"), call
   `get_disposition_board(market)` — public data, so every caller sees the whole board. It buckets
   stocks into 風險股/處置中/即將出關 with price, institutional flow, margin ratio and a rule-based
   "days until next disposition" projection. Always carry the row's `projection_caveat`: it is a
   documented rule of thumb, not an exchange figure and not a price forecast.
4. For a single stock the announcement stream (step 2) has no 注意股 status or projection; say so
   and point to the board if the caller needs that view.
5. Synthesize using `output_structure`, in order. If the window returns no rows, state that
   explicitly rather than presenting an empty section with no explanation.
