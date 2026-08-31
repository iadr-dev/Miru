---
name: etf-compare
version: 1.0.0
description: Compare two ETFs on fund-level metadata + internal fees + holdings/money-flow, plus an estimated 折溢價 for active ETFs (受益人數/配息率 still not covered; NAV is an estimate, not official).
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
tools: [get_canonical_context, get_source_health, get_etf_fund_info, get_etf_expense_ratio, get_etf_money_flow, get_active_etf_report]
output_structure:
  - '基金基本資料對比: 發行人/追蹤指數/流通單位數/成立日期/保管機構/經理人 (兩檔並列)'
  - '內扣費用對比: 經理費/保管費，及近似總費用率(=經理費+保管費，為下限，非查核總費用率)'
  - '操作日報對比: 若兩檔皆為主動式ETF，各自的新增/刪除/加碼/減碼與基金規模變化 (Sponsor)'
  - '折溢價對比: 主動式ETF 的估算折溢價 (=持股市值÷發行單位數 對比市價)，須標注為估算值、非發行商公告 NAV'
  - '跨ETF資金流向脈絡: 兩檔在同一資金流向排行中的相對位置 (Sponsor)'
  - '尚無法提供的比較維度: 官方 NAV/IOPV、受益人數、配息率 (明確列出，不得估算)'
freshness_rule: 每項比較數字須標明資料日期；資金流向彙總限制在 30 天內的區間，操作日報限單一交易日；內扣費用為 SITCA 月頻揭露。
boundaries:
  - 官方 NAV/IOPV、受益人數、配息率目前無對應工具，一律列在 missing_fields，不得憑空估算。
  - 折溢價僅對主動式ETF可估算，且為估算值(未計現金/應收股利/應計費用/匯率，以收盤價計)，須明確標注非官方 NAV；被動式/槓桿型ETF無法估算，列入 missing_fields。
  - 近似總費用率為 經理費+保管費 的下限，未含其他營運費用，非經會計師查核之總費用率(TER)。
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
   outstanding, inception/listed dates, custodian bank, and the fund manager. This is
   fund-level metadata only, never per-constituent holdings or official NAV/IOPV (the adapter
   documents that gap).
3. Call `get_etf_expense_ratio(etf_id, market)` for each — 經理費/保管費 and the approximate
   total-expense-ratio floor (經理費+保管費). Say plainly that this floor excludes other
   operating costs and is not the audited TER; if SITCA doesn't disclose a fund, leave the fee
   fields blank rather than guessing.
4. If both ETFs are active-management funds, call `get_active_etf_report(etf_id, market,
   trade_date)` for each — per-holding 新增/刪除/加碼/減碼 status and fund-size change vs the
   prior day (Sponsor-tier). This also returns an ESTIMATED 折溢價
   (`estimated_premium_discount_pct`) + estimated NAV/unit with an `estimation_caveat`: surface
   it only WITH that caveat (estimate, not official NAV). If either ETF isn't an
   active-management fund, say the operations report and the 折溢價 estimate don't apply to it
   rather than presenting an empty/misleading section.
5. Call `get_etf_money_flow(market, window_start, window_end, top_n)` once (bounded ≤30 days)
   and check whether either ETF's constituents appear prominently in the inflow/outflow
   ranking — this places both ETFs' recent moves in a shared cross-ETF context, it does not
   itself compare the two ETFs directly.
6. Explicitly disclose, in a "尚無法提供" section, that official NAV/IOPV, 受益人數, and 配息率
   have no real tool today — never fabricate a number for any of these to "complete" the
   comparison.
7. Present the two ETFs side by side per `output_structure`, in order. Never rank or recommend
   one ETF over the other — describe differences only.
