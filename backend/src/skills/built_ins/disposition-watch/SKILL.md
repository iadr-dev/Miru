---
name: disposition-watch
version: 1.0.0
description: 公布處置有價證券 status stream for one stock (narrowed — 注意股 side and any escalation forecast are not covered).
triggers: [處置, 處置股, 注意股, disposition, disposition securities]
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
tools: [get_canonical_context, get_source_health, get_disposition_securities]
output_structure:
  - '處置公告紀錄: 區間內處置有價證券公告事件列表'
  - '尚無法提供的部分: 注意股狀態、"最快 N 日後進入處置" 預測 (明確說明無對應資料源)'
freshness_rule: 每筆處置公告須標明公告/處置起訖日期；若查詢區間內沒有公告，明確說明"此區間無處置公告"而非留白。
boundaries:
  - 本技能範疇窄於原始設計：TPEX 注意股狀態與「最快 N 日後進入處置」預測目前無對應資料源，一律列在 missing_scope，不得臆測或外推。
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
3. Explicitly disclose, in a "尚無法提供" section, that the TPEX 注意股 (attention-stock) side
   and any "最快 N 日後進入處置" escalation forecast have no adapter today — no TPEX 注意股
   source exists to fold in, and this tool computes no forecast of its own.
4. Synthesize using `output_structure`, in order. If the window returns no rows, state that
   explicitly rather than presenting an empty section with no explanation.
