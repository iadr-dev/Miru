---
name: company-profile
version: 1.0.0
description: 個股資訊卡 — sector/industry classification, MOPS identity fields (TW), latest announced dividend, market cap/PER/dividend-yield/EPS, and analyst consensus/ESG score for one stock (missing fields disclosed, never fabricated).
triggers: [公司概況, 產業分類, 股利, 配息, 董座, 資本額, 發言人, 簽證會計師, 目標價, ESG, company profile, sector, dividend, chairman, analyst consensus, esg]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: true
tools: [get_canonical_context, get_company_profile, get_dividend_schedule, get_analyst_consensus, get_esg_score]
output_structure:
  - '產業分類: sector / industry'
  - '公司基本資料 (TW): 董座/總經理/發言人/簽證會計師/資本額/成立日期/總部/官網 — 僅 TW 個股 (MOPS)，其他市場一律列入 missing_fields'
  - '市值與估值: 市值、PER、股利率、EPS'
  - '最近一次股利: 金額與宣告日期'
  - '分析師共識: 目標價區間、評等分佈 (若有覆蓋)'
  - 'ESG 分數: Sustainalytics 風險分數 (若有覆蓋)'
  - '尚無法提供的欄位: 明確列出 (revenue_yoy/margins，以及非 TW 市場的 MOPS 欄位)'
freshness_rule: 股利資訊須標明宣告/發放日期；MOPS 基本資料標明其 currentness_caveat (whole-market current dump, 無歷史)；缺漏欄位一律列在 missing_fields，不得臆測。
boundaries:
  - 不提供目標價、評等或買賣建議 — analyst_consensus 僅陳述第三方已發布的目標價/評等數據，不得由本技能自行解讀為買賣訊號。
  - 缺漏欄位（如 ESG 分數、分析師覆蓋、非 TW 的 MOPS 欄位）一律誠實列出，不得杜撰數字填補。
resources: []
outputs: markdown
---

# Company profile

## Procedure

1. Call `get_canonical_context(stock_id, market)` for the currentness baseline.
2. Call `get_company_profile(stock_id, market)` — sector/industry, latest dividend, market
   value/PER/dividend-yield/EPS, and (TW only) MOPS identity fields (chairman/general manager/
   spokesperson/auditor/capital/founding date/headquarters/website/industry code). For a
   non-TW symbol, every MOPS-sourced field is absent by design — always read and surface its
   `missing_fields` list verbatim, in a clearly labeled "not yet available" section, rather
   than presenting a profile that looks complete when it isn't.
3. Call `get_analyst_consensus(stock_id, market)` for the target-price/rating consensus (real
   for any market, not just non-TW) — present the mean/median/high/low target and analyst
   count when covered; state plainly "no analyst coverage" when `consensus` is null, never
   silently omit the section.
4. Call `get_esg_score(stock_id, market)` for the ESG risk score — sparse, large-cap-skewed
   coverage; state plainly "no ESG data" when `esg` is null.
5. If the caller specifically asked about dividends, call `get_dividend_schedule(stock_id,
   market)` for a fuller history than the single latest-dividend field the profile tool
   carries, and cite each entry's declared/paid date.
6. Present sector/industry first, then (TW) the MOPS identity card, then market value/
   valuation, then the dividend read, then analyst consensus/ESG, then the missing-fields
   disclosure. Never imply completeness the underlying data doesn't have.
