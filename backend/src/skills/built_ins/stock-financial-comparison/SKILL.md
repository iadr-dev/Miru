---
name: stock-financial-comparison
version: 1.0.0
description: Side-by-side revenue-trend, seasonality, and dividend-consistency comparison for two stocks (balance-sheet/cash-flow depth is out of scope — use earnings-quality per symbol for that).
triggers: [比較兩檔股票, 股票比較, 比較股票, compare stocks, stock comparison, compare two stocks]
design_pattern: pipeline
inputs:
  stock_id_a:
    type: string
    required: true
  stock_id_b:
    type: string
    required: true
  start_date:
    type: string
    required: false
  end_date:
    type: string
    required: false
tools: [get_canonical_context, get_source_health, get_month_revenue, get_dividend_policy_summary]
output_structure:
  - '營收趨勢對比: 兩檔股票近5年月營收 MoM/YoY 走勢並列'
  - '季節性對比: 兩檔各自的旺季/淡季月份與變異百分比並列'
  - '股利穩定性對比: 兩檔各自近5年配息年度覆蓋率、現金/股票股利比例、填息成功率並列'
  - '尚無法提供的比較維度: 資產負債表/現金流細節 (明確說明改用 earnings-quality 技能個股查詢)'
  - '綜合描述性讀取: 兩檔在營收趨勢/季節性/股利穩定性上的結構性差異描述 (非優劣排序，非買賣建議)'
freshness_rule: 每項比較數字須標明資料日期；兩檔股票須使用同一時間窗口，確保並列比較公平。
boundaries:
  - 本技能刻意排除資產負債表/現金流比較 (out of scope，以維持工具呼叫量在單一技能的步數上限內) — 需要該深度請改用 earnings-quality 技能，逐一查詢兩檔個股。
  - 季節性變異% 為 (該月營收 - 該月5年平均) ÷ 該月5年平均，公式須內嵌顯示；不得使用「旺季/淡季」暗示買賣時機。
  - 股利穩定性為描述性讀取，不得轉換為評等或投資建議。
  - 僅描述兩檔之間的結構性差異，不建議應優先持有哪一檔，不給出配置比例，不做買賣建議。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Stock financial comparison (revenue/seasonality/dividend only)

## Procedure

1. Call `get_canonical_context` + `get_source_health` for both `stock_id_a` and `stock_id_b` —
   if either flags a stale/unreachable source, say so before presenting any comparison.
2. Call `get_month_revenue(stock_id, market, start_date, end_date)` for each stock, with a window
   spanning at least the last 5 fiscal years (default to 5 years ending at `built_at`'s date if
   the caller gave no `start_date` — state the actual window used, and use the SAME window for
   both stocks so the comparison is fair). Cite each result's `trend` field's `mom_pct`/`yoy_pct`
   directly — never hand-diff raw `rows`.
3. From each stock's `trend`, group filed months by calendar month across the window and compute
   each month's 5-year average revenue, per stock. Identify each stock's own 旺季/淡季 months and
   variance (`(該月營收 - 該月5年平均) ÷ 該月5年平均`, formula shown inline), then present the two
   stocks' seasonality patterns side by side — note explicitly if one stock's revenue is far more
   seasonal than the other's.
4. Call `get_dividend_policy_summary(stock_id, market, start_date, end_date)` for each stock, same
   window. Cite `year_summaries` directly for "distributed dividends in N of the last 5 fiscal
   years" per stock, and `stats`'s `cash_dividend_yield_pct`/`payout_ratio_pct`/`fill_rate_pct`
   for the headline stability figures — describe consistency in plain descriptive language, never
   as a star rating or numeric score (the tool provides none), and present both stocks' figures
   side by side.
5. Explicitly disclose, in a "尚無法提供" section, that this skill does not compare balance-sheet
   or cash-flow detail — direct the caller to run `earnings-quality` per symbol for that depth,
   rather than presenting an empty or estimated comparison for it.
6. Synthesize using `output_structure`, in order. The closing section must state a clear,
   specific descriptive/comparative characterization of the structural differences between the
   two stocks (e.g. "structurally the more seasonal of the two, concentrated in Q4" or "the more
   consistent dividend payer of the two, with a higher fill rate") per the governance prompt's
   decisive-descriptive-language rule — never a ranking of which to hold, an allocation split, or
   any buy/sell/hold framing.
