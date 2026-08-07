---
name: active-etf-flow
version: 1.0.0
description: 主動式ETF 操作日報 for one ETF, plus cross-ETF money-flow consensus for its constituents (流入/流出, 加碼/減碼, 多空共識).
triggers: [主動式ETF, 操作日報, 資金流向, 加碼, 減碼, active ETF, holdings change]
design_pattern: pipeline
inputs:
  etf_id:
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
tools: [get_canonical_context, get_source_health, get_active_etf_report, get_etf_money_flow]
output_structure:
  - '操作日報: 新增/刪除/加碼/減碼 持股與權重變化 (單一 ETF)'
  - '基金規模: 較前日變化 (折溢價說明為何無法提供)'
  - '跨 ETF 資金流向: 本 ETF 主要持股在同類 ETF 間的流入/流出排行'
  - '多空共識: 買超/賣超 ETF 檔數對比 (描述，非買賣訊號)'
freshness_rule: 操作日報與資金流向皆須標明資料日期範圍；跨 ETF 彙總限制在 30 天內的區間。
boundaries:
  - 僅描述持股與資金流變化，不提供進出場價位。
  - 折溢價欄位目前無法提供時，明確說明原因，不得憑空估算。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Active-ETF flow

## Procedure

1. Call `get_canonical_context(etf_id, market)` for a currentness baseline, then
   `get_source_health(etf_id, market)` — if either flags a stale/unreachable source, say so
   before presenting numbers.
2. Call `get_active_etf_report(etf_id, market, trade_date)` (omit `trade_date` for "latest
   available"). Report per-holding 新增/刪除/加碼/減碼 status and the fund-size change vs the
   prior day. This tool's `premium_discount_pct` is always `None` today — when present, surface
   its `premium_discount_unavailable_reason` verbatim rather than omitting the field silently.
3. Call `get_etf_money_flow(market, start_date, end_date, top_n)` for a bounded window
   (≤30 days — the tool itself enforces this) covering the same period, to place this ETF's
   constituent moves in the context of cross-ETF consensus (how many other active ETFs are
   buying/selling the same names). This tool is Sponsor-tier — if unentitled, report around
   the masked section.
4. Synthesize using the `output_structure` sections above. Every figure carries its as-of date.
   Never infer "smart money" motive from a flow pattern — describe the pattern only.
