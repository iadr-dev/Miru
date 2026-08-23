---
name: earnings-quality
version: 1.0.0
description: Revenue-trend + balance-sheet/cash-flow-derived margin/quality read for one stock, plus its settled ex-dividend results.
triggers: [營收, 月營收, 財報, 財務品質, 現金流, 毛利率, earnings quality, revenue trend, cash flow]
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
tools: [get_canonical_context, get_source_health, get_financial_statement, get_month_revenue, get_dividend_result]
output_structure:
  - '月營收趨勢: MoM/YoY 變化與近期走勢描述'
  - '資產負債表摘要: 關鍵科目與變化 (若 statement_type=balance_sheet)'
  - '現金流量表摘要: 營運/投資/籌資活動現金流 (若 statement_type=cash_flow)'
  - '除權除息結果: 近期已結算股利與股價調整幅度'
  - '品質觀察: 營收與現金流是否一致的描述性讀取 (非評等或買賣建議)'
freshness_rule: 月營收與除權除息結果須標明申報/結算日期；資產負債表/現金流須標明 period_end；缺一律標明資料截止時間。
boundaries:
  - 本工具不提供 ROE/FCF 等衍生比率的現成欄位 — 若需要，必須在回覆中清楚說明是由原始科目自行推算，並附上推算方式。
  - 不提供 EPS 預估、目標價或評等；僅描述已公布/已結算的歷史數字。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Earnings quality

## Procedure

1. Call `get_canonical_context(stock_id, market)` for a currentness baseline, then
   `get_source_health(stock_id, market)` — surface any stale/unreachable source first.
2. Call `get_month_revenue(stock_id, market, start_date, end_date)` for the 月營收 filing
   history. Cite the returned `trend` field's `mom_pct`/`yoy_pct` directly — it is already
   pre-computed per filed month server-side. Never hand-diff adjacent `rows` yourself: a long
   window's raw rows are enough to exhaust this turn's tool-result render budget on their own,
   leaving no room to write the actual synthesis the caller asked for.
3. Call `get_financial_statement(stock_id, market, statement_type, start_date, end_date)` —
   this tool serves **balance_sheet or cash_flow only** (the income statement is not this
   tool's scope; if the caller wants margin/EPS detail, note that only revenue and dividend
   history are available through this skill's other two tools). Call it once per
   `statement_type` the caller cares about (balance_sheet and/or cash_flow) and report the line
   items returned, each tagged with its `period_end`.
4. Call `get_dividend_result(stock_id, market, start_date, end_date)` for the settled 除權除息
   outcomes (distinct from the announced-policy schedule) — before/after price and the
   dividend amount actually applied.
5. Synthesize using `output_structure`, in order. Any derived ratio (margin/ROE/FCF-style read)
   must show its inputs and formula inline — never present a derived number as if it came
   directly from a tool that doesn't compute it. Never translate a revenue/cash-flow trend into
   a buy/sell/hold call.
