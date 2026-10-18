---
name: active-etf-flow
version: 1.1.0
description: 主動式ETF 操作日報 for one ETF, plus cross-ETF money-flow consensus for its constituents (流入/流出, 加碼/減碼, 多空共識), the whole-market 當日操作訊號, and which active ETFs hold a given stock.
triggers: [主動式ETF, 操作日報, 資金流向, 加碼, 減碼, 當日操作訊號, 哪些主動式ETF持有, 主動式ETF持股, active ETF, holdings change, daily signal, which etfs hold]
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
tools: [get_canonical_context, get_source_health, get_active_etf_report, get_etf_money_flow, get_daily_signal, get_etfs_holding_stock]
output_structure:
  - '操作日報: 新增/刪除/加碼/減碼 持股與權重變化 (單一 ETF)'
  - '全市場當日操作訊號 (使用者問「今天主動式 ETF 在買賣什麼」時): 各股新增/刪除/加碼/減碼狀態、買賣 ETF 共識與流入/流出重點 (Sponsor)'
  - '持有該股的主動式 ETF (使用者問某檔股票被哪些 ETF 持有時): 持股數排行、占 ETF 權重排行與近期持股異動 (Sponsor)'
  - '基金規模: 較前日變化'
  - '估算折溢價: 估算淨值(=持股市值÷發行單位數)對比市價，須標注為估算值、非發行商公告 NAV'
  - '跨 ETF 資金流向: 本 ETF 主要持股在同類 ETF 間的流入/流出排行'
  - '多空共識: 買超/賣超 ETF 檔數對比 (描述，非買賣訊號)'
freshness_rule: 操作日報與資金流向皆須標明資料日期範圍；跨 ETF 彙總限制在 30 天內的區間。
boundaries:
  - 僅描述持股與資金流變化，不提供進出場價位。
  - get_daily_signal 與 get_etfs_holding_stock 為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明；兩者的買賣 ETF 共識只是描述各 ETF 的持股變動，不是對該股的買賣訊號。
  - 折溢價為估算值(未計現金/應收股利/應計費用/匯率，以收盤價計)，須明確標注非官方 NAV/IOPV；估算所需欄位缺漏時明確說明，不得憑空補值。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Active-ETF flow

## Procedure

1. Call `get_canonical_context(etf_id, market)` for a currentness baseline, then
   `get_source_health(etf_id, market)` — if either flags a stale/unreachable source, say so
   before presenting numbers. Its `built_at` timestamp is the anchor for "today" in step 3
   below — never substitute your own notion of the current date, which can silently drift from
   wall-clock time.
2. Call `get_active_etf_report(etf_id, market, trade_date)` (omit `trade_date` for "latest
   available"). Report per-holding 新增/刪除/加碼/減碼 status and the fund-size change vs the
   prior day. The official `premium_discount_pct` is always `None` (no NAV/IOPV source); the tool
   instead returns an ESTIMATED `estimated_premium_discount_pct` + `estimated_nav_per_unit` with
   an `estimation_caveat` — surface those only together WITH that caveat (an estimate, never the
   issuer's official NAV), and when they're `None` say the estimate's inputs were unavailable
   rather than omitting silently.
3. Call `get_etf_money_flow(market, start_date, end_date, top_n)` for a bounded window
   (≤30 days — the tool itself enforces this) covering the same period, to place this ETF's
   constituent moves in the context of cross-ETF consensus (how many other active ETFs are
   buying/selling the same names). `start_date`/`end_date` are required by this tool — if the
   caller gave no `window_start`/`window_end`, default to the 30-day maximum window ending at
   `built_at`'s date, and state the default explicitly, including the actual end date used.
   This tool is Sponsor-tier — if unentitled, report around the masked section.
4. Synthesize using the `output_structure` sections above. Every figure carries its as-of date.
   Never infer "smart money" motive from a flow pattern — describe the pattern only.
5. For a whole-market question ("今天主動式 ETF 加碼/減碼了哪些股票"), call
   `get_daily_signal(market, trade_date)` (Sponsor-tier) — the per-stock status aggregated across
   every active ETF, with the buying/selling ETF consensus and top inflow/outflow highlights.
   Omit `trade_date` for the latest day and state which day it was.
6. For "which active ETFs hold stock X", call `get_etfs_holding_stock(id, market, as_of)`
   (Sponsor-tier) with the stock id (not an ETF id): the holding-shares ranking, weight-in-ETF
   ranking and recent holding-change events. Describe who holds and how holdings changed; do not
   infer a view on the stock from it.
