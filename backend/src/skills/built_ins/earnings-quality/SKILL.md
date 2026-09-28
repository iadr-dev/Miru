---
name: earnings-quality
version: 1.1.0
description: Revenue-trend + balance-sheet/cash-flow-derived margin/quality read for one stock, plus a multi-year seasonality read and dividend-policy consistency summary.
triggers: [營收, 月營收, 財報, 財務品質, 現金流, 毛利率, 季節性, 淡季, 旺季, 股利穩定性, earnings quality, revenue trend, cash flow, seasonality, dividend consistency]
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
tools: [get_canonical_context, get_source_health, get_financial_statement, get_month_revenue, get_dividend_policy_summary]
output_structure:
  - '月營收趨勢: MoM/YoY 變化與近期走勢描述'
  - '季節性分析: 依月份彙總近5年月營收，指出旺季/淡季月份與其平均營收相對全年均值的變異百分比'
  - '資產負債表摘要: 關鍵科目與變化 (若 statement_type=balance_sheet)'
  - '現金流量表摘要: 營運/投資/籌資活動現金流 (若 statement_type=cash_flow)'
  - '股利穩定性讀取: 近5年配息年度覆蓋率、現金/股票股利比例、填息成功率 — 描述性讀取，非星等評分（工具不提供星等，以文字描述穩定程度）'
  - '品質觀察: 營收、現金流與股利是否一致的描述性讀取 (非評等或買賣建議)'
freshness_rule: 月營收與股利事件須標明申報/公告日期；資產負債表/現金流須標明 period_end；缺一律標明資料截止時間。
boundaries:
  - 本工具不提供 ROE/FCF 等衍生比率的現成欄位 — 若需要，必須在回覆中清楚說明是由原始科目自行推算，並附上推算方式。
  - 季節性變異% 為 (該月營收 - 該月5年平均) ÷ 該月5年平均，公式須內嵌顯示；不得使用「旺季/淡季」暗示買賣時機。
  - 配息穩定性為描述性讀取，不得轉換為評等或投資建議。
  - 不提供 EPS 預估、目標價或評等；僅描述已公布/已結算的歷史數字。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Earnings quality

## Procedure

1. Call `get_canonical_context(stock_id, market)` for a currentness baseline, then
   `get_source_health(stock_id, market)` — surface any stale/unreachable source first.
2. Call `get_month_revenue(stock_id, market, start_date, end_date)` with a window spanning at
   least the last 5 fiscal years (default to 5 years ending at `built_at`'s date if the caller
   gave no `start_date` — state the actual window used). Cite the returned `trend` field's
   `mom_pct`/`yoy_pct` directly — it is already pre-computed per filed month server-side. Never
   hand-diff adjacent `rows` yourself: a long window's raw rows are enough to exhaust this turn's
   tool-result render budget on their own, leaving no room to write the actual synthesis the
   caller asked for.
3. From that same `trend` result, group filed months by calendar month (Jan, Feb, ... Dec) across
   the full window and compute each month's average revenue. Identify the month(s) with the
   highest and lowest 5-year average as the 旺季/淡季 — state the variance explicitly as
   `(該月營收 - 該月5年平均) ÷ 該月5年平均`, showing the formula inline, never just the resulting
   percentage. This is a descriptive seasonality read of filing timing, never a signal for when
   to trade.
4. Call `get_financial_statement(stock_id, market, statement_type, start_date, end_date)` —
   this tool serves **balance_sheet or cash_flow only** (the income statement is not this
   tool's scope; if the caller wants margin/EPS detail, note that only revenue and dividend
   history are available through this skill's other two tools). Call it once per
   `statement_type` the caller cares about (balance_sheet and/or cash_flow) and report the line
   items returned, each tagged with its `period_end`.
5. Call `get_dividend_policy_summary(stock_id, market, start_date, end_date)` with the same
   multi-year window — cite `year_summaries` directly for "distributed dividends in N of the
   last 5 fiscal years" and the cash/stock split per year, `stats`'s `cash_dividend_yield_pct`/
   `payout_ratio_pct`/`fill_rate_pct` for the headline stability figures, and `fill_results` for
   per-event 填息/填權 resolution — never hand-recompute any of these from `events` yourself.
   Describe consistency in plain descriptive language (e.g. "distributed a dividend in 4 of the
   last 5 fiscal years, with a fill rate of X%") — never as a star rating or numeric score, since
   the tool provides none.
6. Synthesize using `output_structure`, in order. Any derived ratio (margin/ROE/FCF-style read)
   must show its inputs and formula inline — never present a derived number as if it came
   directly from a tool that doesn't compute it. Never translate a revenue/cash-flow/seasonality/
   dividend read into a buy/sell/hold call — state a clear descriptive characterization instead
   (e.g. "structurally seasonal, concentrated in Q4" or "a consistent dividend payer with a high
   fill rate") per the governance prompt's decisive-descriptive-language rule.
