---
name: company-profile
version: 1.0.0
description: 個股資訊卡 — sector/industry classification and the latest announced dividend for one stock (narrowed to what's actually backed today; missing fields disclosed, never fabricated).
triggers: [公司概況, 產業分類, 股利, 配息, company profile, sector, dividend]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: true
tools: [get_canonical_context, get_company_profile, get_dividend_schedule]
output_structure:
  - '產業分類: sector / industry'
  - '最近一次股利: 金額與宣告日期'
  - '尚無法提供的欄位: 明確列出 (董座/資本額/PE/PB/... 等)'
freshness_rule: 股利資訊須標明宣告/發放日期；缺漏欄位一律列在 missing_fields，不得臆測。
boundaries:
  - 不提供目標價、評等或買賣建議。
  - 缺漏欄位（如董座、資本額、PE、PB）一律誠實列出，不得杜撰數字填補。
resources: []
outputs: markdown
---

# Company profile (narrow, honest version)

## Procedure

1. Call `get_canonical_context(stock_id, market)` for the currentness baseline.
2. Call `get_company_profile(stock_id, market)`. This tool intentionally covers only
   sector/industry classification and the latest announced dividend — it is not the full MOPS
   identity-card design (no 董座/資本額/PE/PB fields exist behind it yet). Always read and
   surface its `missing_fields` list verbatim, in a clearly labeled "not yet available"
   section, rather than presenting a profile that looks complete when it isn't.
3. If the caller specifically asked about dividends, call `get_dividend_schedule(stock_id,
   market)` for a fuller history than the single latest-dividend field the profile tool
   carries, and cite each entry's declared/paid date.
4. Present sector/industry first, then the dividend read, then the missing-fields disclosure.
   Never imply completeness the underlying data doesn't have.
