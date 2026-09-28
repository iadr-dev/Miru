---
name: tdcc-registry-lookup
version: 1.0.0
description: TDCC (集保) reference/registry datasets for one stock — securities master record, custody share-split, free weekly shareholding distribution, shareholder-meeting e-voting window, whole-market monthly custody-account stats, offshore-fund NAV, and a raw-dataset escape hatch for any other TDCC OpenData id.
triggers: [集保, 集保股份, 集保戶股權分散, 股東會電子投票, 境外基金淨值, 集保帳戶統計, TDCC, custody shares, e-voting, offshore fund nav]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: false
  market:
    type: string
    required: true
  dataset_id:
    type: string
    required: false
tools: [get_source_health, get_tdcc_securities_info, get_tdcc_pledged_custody_shares, get_tdcc_shareholding_distribution, get_tdcc_evoting_schedule, get_tdcc_monthly_custody_accounts, get_tdcc_offshore_fund_nav, get_tdcc_raw_dataset]
output_structure:
  - '證券基本資料: market/registrar/status/par-value 參考記錄 (非價格)'
  - '集保股份保管明細: 分戶/集保股數拆分 (僅部分證券有此拆分資料)'
  - '集保戶股權分散表: 免費週資料的股權分散層級 (與 Sponsor 級 FinMind 版本為不同資料集)'
  - '股東會電子投票起訖日: 僅為投票窗口排程，非投票結果'
  - '集保帳戶統計: 全市場月度新增/註銷/總計帳戶數 (market-level, 非個股)'
  - '境外基金淨值: 若查詢對象是 TDCC 基金代碼而非個股'
  - '原始資料集查詢: 若查詢的是上述皆未覆蓋的 TDCC OpenData 資料集 id'
freshness_rule: 每筆記錄須標明其資料日期或 currentness_caveat；集保帳戶統計與境外基金淨值為市場層級/基金層級資料，非個股逐日資料。
boundaries:
  - get_tdcc_shareholding_distribution 為免費週資料，與 margin-and-short-interest 技能使用的 Sponsor 級 FinMind 股權分散 (get_shareholder_dispersion) 是不同資料集，不得混用或互相取代。
  - get_tdcc_offshore_fund_nav 的輸入代碼是 TDCC 自訂基金代碼，不是一般股票代碼；若使用者提供的是股票代碼，改用其他分頁。
  - get_tdcc_raw_dataset 僅回傳解析後的 CSV 表頭與列，一律標明來源資料集 id，絕不將其包裝成任何一種既有的個股分析結論。
  - 集保股份保管明細僅涵蓋 TDCC 有回報拆分的證券子集，查無資料時明確說明「此證券無拆分資料」而非留白。
  - 股東會電子投票起訖日僅為排程資料，不提供投票結果或提案內容。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# TDCC registry lookup

## Procedure

1. Call `get_source_health(stock_id or "", market)` as a currentness baseline before presenting
   any figures.
2. If the caller wants a stock's basic TDCC registry facts, call `get_tdcc_securities_info(
   stock_id, market)` — a static reference row (market/registrar/status/par value), never a
   price.
3. If the caller asks about custody-share structure, call `get_tdcc_pledged_custody_shares(
   stock_id, market)` — covers only the subset of securities TDCC reports a dispersed-vs-
   specific split for; say so plainly when a symbol has no such row rather than treating an
   empty result as an error.
4. If the caller wants a free chip-concentration read, call `get_tdcc_shareholding_distribution(
   stock_id, market)` — this is the free weekly tier, explicitly distinct from the Sponsor-tier
   FinMind equivalent used by the `margin-and-short-interest` skill; never present the two as
   interchangeable or average them together.
5. If the caller asks about an upcoming shareholder meeting's e-voting window, call
   `get_tdcc_evoting_schedule(stock_id, market)` — report the meeting date and voting start/end
   only; this dataset carries no vote tally or proposal content.
6. If the caller asks about whole-market custody-account counts (not a per-symbol question),
   call `get_tdcc_monthly_custody_accounts(market)` — a market-level monthly aggregate, never a
   per-symbol fan-out.
7. If the caller names a TDCC offshore-fund code (not an equity ticker) and wants its NAV, call
   `get_tdcc_offshore_fund_nav(fund_code, market)` — note this carries the fund's own reporting
   lag, never a live quote.
8. If none of the above cover what the caller asked for and they can name (or you can infer) a
   specific TDCC OpenData dataset id, call `get_tdcc_raw_dataset(dataset_id, symbol_column,
   symbol_value, limit)` as the escape hatch — always cite the returned `dataset_id` explicitly
   in the response rather than presenting the parsed rows as if they came from a dedicated tool.
9. Synthesize using `output_structure`, reporting only the section(s) the query actually needs —
   this is a reference-lookup skill, not a report generator.
