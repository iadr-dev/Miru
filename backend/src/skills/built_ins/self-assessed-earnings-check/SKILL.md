---
name: self-assessed-earnings-check
version: 1.0.0
description: MOPS 自結損益公告 (self-assessed/preliminary P&L) filing check for one stock, one monthly period at a time — a voluntary, unaudited figure, not a confirmed final result.
triggers: [自結損益, 自結營收, 預估損益, self-assessed earnings, preliminary earnings, preliminary p&l]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: true
  period:
    type: string
    required: true
  security_type:
    type: string
    required: true
tools: [get_source_health, get_self_assessed_earnings]
output_structure:
  - '自結損益公告: 指定月份的自結損益數字與公告日期 (若有申報)'
  - '性質提醒: 這是公司自願、未經會計師確認的初步數字，非正式財報結果'
freshness_rule: 若該月份查無申報，明確說明「該公司此期間未申報自結損益」而非留白；若有申報，標明公告日期。
boundaries:
  - "get_self_assessed_earnings 一次僅服務一個月份、一家交易所 (security_type: twse 上市 / tpex 上櫃) — 若使用者想看多個月份，逐月重複呼叫並明確標明這是多次單月查詢的彙整。"
  - 這是公司自願申報的初步/未經會計師確認數字，不得與正式財報 (earnings-quality 技能的 get_financial_statement/get_month_revenue) 混為一談或直接比較為同等級數字。
  - 不提供 EPS 預估、目標價或評等；僅描述已公布的自結數字本身。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Self-assessed earnings check

## Procedure

1. Call `get_source_health(stock_id, market)` as a currentness baseline.
2. Call `get_self_assessed_earnings(stock_id, market, period, security_type)` — `period` is an
   ISO "YYYY-MM" monthly period, and `security_type` must be `"twse"` (上市) or `"tpex"` (上櫃);
   if the caller hasn't specified which exchange the stock lists on, infer it from context or
   ask rather than guessing silently.
3. If no filing exists for that period, state plainly that the company did not file a
   self-assessed/preliminary P&L for that month — never imply this means the company has no
   earnings at all, only that this particular voluntary disclosure wasn't made.
4. If a filing exists, report the figure with its announcement date, and explicitly label it as
   a voluntary, unaudited preliminary number — never present it with the same confidence as a
   confirmed financial-statement figure.
5. Synthesize using `output_structure`, in order.
