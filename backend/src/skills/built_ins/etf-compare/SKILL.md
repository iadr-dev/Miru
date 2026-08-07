---
name: etf-compare
version: 1.0.0
description: Compare two ETFs on fund-level metadata + holdings/money-flow (narrowed — NAV/折溢價/受益人數/fees/yield are not covered).
triggers: [ETF比較, ETF對比, 比較兩檔ETF, etf compare, compare etfs]
design_pattern: pipeline
inputs:
  etf_id_a:
    type: string
    required: true
  etf_id_b:
    type: string
    required: true
  trade_date:
    type: string
    required: false
  window_start:
    type: string
    required: false
  window_end:
    type: string
    required: false
tools: [get_canonical_context, get_source_health, get_etf_fund_info, get_etf_money_flow, get_active_etf_report]
output_structure:
  - '基金基本資料對比: 發行人/追蹤指數/流通單位數 (兩檔並列)'
  - '操作日報對比: 若兩檔皆為主動式ETF，各自的新增/刪除/加碼/減碼與基金規模變化 (Sponsor)'
  - '跨ETF資金流向脈絡: 兩檔在同一資金流向排行中的相對位置 (Sponsor)'
  - '尚無法提供的比較維度: NAV/折溢價/受益人數/管理費/配息率 (明確列出，不得估算)'
freshness_rule: 每項比較數字須標明資料日期；資金流向彙總限制在 30 天內的區間，操作日報限單一交易日。
boundaries:
  - 本技能範疇窄於原始設計：NAV、折溢價、受益人數、管理費、配息率目前無對應工具，一律列在 missing_fields，不得憑空估算。
  - get_active_etf_report 與 get_etf_money_flow 為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明。
  - 僅描述持股/資金流差異，不建議應優先持有哪一檔或給出配置比例。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# ETF compare (narrow, honest version)

## Procedure

1. Call `get_canonical_context` + `get_source_health` for both `etf_id_a` and `etf_id_b` — if
   either flags a stale/unreachable source, say so before presenting any comparison.
2. Call `get_etf_fund_info(etf_id, market)` for each ETF — issuer, tracked index, units
   outstanding. This is fund-level metadata only, never per-constituent holdings or NAV/IOPV
   (the adapter itself documents that gap).
3. If both ETFs are active-management funds, call `get_active_etf_report(etf_id, market,
   trade_date)` for each — per-holding 新增/刪除/加碼/減碼 status and fund-size change vs the
   prior day (Sponsor-tier). If either ETF isn't an active-management fund, say the operations
   report doesn't apply to it rather than presenting an empty/misleading section.
4. Call `get_etf_money_flow(market, window_start, window_end, top_n)` once (bounded ≤30 days)
   and check whether either ETF's constituents appear prominently in the inflow/outflow
   ranking — this places both ETFs' recent moves in a shared cross-ETF context, it does not
   itself compare the two ETFs directly.
5. Explicitly disclose, in a "尚無法提供" section, that NAV/折溢價/受益人數/管理費/配息率 have
   no real tool today — never fabricate a number for any of these to "complete" the
   comparison.
6. Present the two ETFs side by side per `output_structure`, in order. Never rank or recommend
   one ETF over the other — describe differences only.
