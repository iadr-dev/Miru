---
name: reference-data-lookup
version: 1.0.0
description: Symbol/roster/calendar reference lookups — free-text symbol search (non-TW), whole-market stock/warrant roster, trading-day calendar, active-ETF roster, warrant-to-underlying mapping, and the adapter capability matrix.
triggers: [代碼查詢, 股票代碼, 交易日, 權證對應, symbol search, symbol lookup, trading calendar, roster, source matrix]
design_pattern: tool_wrapper
inputs:
  query:
    type: string
    required: false
  market:
    type: string
    required: true
tools: [get_symbol_search, get_symbol_lookup, get_stock_roster, get_trading_calendar, get_active_etf_roster, get_warrant_underlying, get_source_matrix]
output_structure:
  - '符號搜尋結果: 若為自由文字查詢 (僅 US/GB/JP/EU)'
  - '全市場股票/權證參考表: 若查詢是要確認代碼是否存在 (TW)'
  - '交易日曆: 若查詢與特定交易日相關 (TW)'
  - '主動式ETF名單: 若查詢是要確認某ETF是否為主動式 (TW)'
  - '權證對應標的: 若查詢的是某權證代碼的對應標的股 (TW, Sponsor)'
  - '能力矩陣: 若呼叫者想知道某資料能力目前由哪個來源服務'
freshness_rule: 參考表/名冊本質上是低頻更新的靜態或半靜態資料，仍須標明每次查詢的 currentness_caveat，不得省略。
boundaries:
  - get_symbol_search/get_symbol_lookup 僅服務 US/GB/JP/EU 四個市場，TW 股票代碼查詢請直接使用已知代碼呼叫其他技能，不透過本技能的自由文字搜尋。
  - get_warrant_underlying 為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明。
  - 本技能純粹提供參考性資料查找，不做任何價格、籌碼或財務分析，也不提供買賣建議。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Reference data lookup

## Procedure

1. If the caller has free text (a company name or partial ticker) and the target market is
   US/GB/JP/EU, call `get_symbol_search(query, market)` for broad resolution or
   `get_symbol_lookup(query, market)` when the caller also names an instrument type
   (stock/ETF/index/...) to scope the search.
2. If the caller needs to confirm a TW stock/warrant code exists or find its basic reference
   row, call `get_stock_roster(market="TW")` — a whole-market reference roster, never a
   per-symbol fetch.
3. If the query concerns a specific trading day (is it a trading day, what's the next one),
   call `get_trading_calendar(market)` — whole-market reference table.
4. If the caller wants to know whether a given ETF is active-management, call
   `get_active_etf_roster(market="TW")`.
5. If the caller has a TW warrant code and wants its underlying stock, call
   `get_warrant_underlying(warrant_id, market, start_date, end_date)` (Sponsor-tier) — resolve
   by date interval, not just "the latest row," since the mapping can change.
6. If the caller is asking a meta-question about which source currently backs a capability,
   call `get_source_matrix()` for the adapter capability matrix.
7. Report only the section(s) relevant to the actual query — this skill is a lookup utility,
   not a report generator; keep responses terse and reference-shaped.
