---
name: international-equity-snapshot
version: 1.0.0
description: yfinance-backed non-TW equity/ETF snapshot — options chain, institutional/insider holders, fund holdings, market status, sector/industry overview, calendars, FinMind's own international roster+price, for one US/GB/JP/EU symbol.
triggers: [美股, 國際股票, 選擇權鏈, 機構持股, 產業總覽, 財報日曆, non-TW equity, options chain, sector overview, institutional holders, calendar]
design_pattern: tool_wrapper
inputs:
  symbol_id:
    type: string
    required: true
  market:
    type: string
    required: true
tools: [get_options_chain, get_institutional_holders, get_fund_holdings, get_insider_activity, get_market_status, get_sector_overview, get_industry_overview, get_calendar_events_feed, get_international_stock, get_us_stock_price_minute]
output_structure:
  - '市場狀態: 目前開盤/休市狀態'
  - '選擇權鏈: 最近到期日的買權/賣權 (若查詢的是個股/ETF)'
  - '機構/基金持有人: 機構與共同基金持股列表 (若查詢的是個股)'
  - '基金持股與產業權重: 若查詢的是 ETF/共同基金'
  - '內部人交易: Form-4 derived transactions (美國以外市場通常稀疏或空白)'
  - '產業/類股總覽: 若呼叫者額外提供 sector_key/industry_key'
  - '財報/IPO/經濟日曆: 若查詢者要求該市場近期事件'
  - 'FinMind 國際股票資訊+價格: FinMind 自有的美/英/歐/日股票資訊+價格資料集 (與 yfinance 主線資料是不同來源)'
  - '美股分鐘K: 若查詢的是美股且需要分鐘級價格 (Sponsor)'
freshness_rule: 每個工具各自標明其 currentness_caveat；insider_activity 在美國以外市場經常為空，須明確說明「稀疏/無資料」而非省略欄位；get_international_stock 與主線 yfinance 資料是不同來源，須標明來源差異。
boundaries:
  - 本技能僅服務 US/GB/JP/EU 四個市場 — TW 個股請改用台灣專屬技能 (company-profile/chip-flow-review 等)，不得用本技能的工具處理 TW 標的。
  - get_insider_activity 是美股 Form-4 揭露資料，非台灣董監事持股 (質押比請改用 governance-risk 技能)。
  - 僅描述持股結構與市場狀態，不提供評等、目標價或買賣建議。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# International equity snapshot

## Procedure

1. Confirm `market` is one of US/GB/JP/EU — every tool in this skill validates and rejects any
   other market (including TW), so route TW queries to a TW-specific skill instead.
2. Call `get_market_status(market)` for the region's open/closed status + summary.
3. If the symbol is an individual equity, call `get_options_chain(symbol_id, market, expiry)`
   for the nearest (or requested) expiry's calls+puts, `get_institutional_holders(symbol_id,
   market)` for institutional/mutual-fund holder rows, and `get_insider_activity(symbol_id,
   market)` for Form-4-derived insider transactions — note explicitly when this list is
   sparse/empty outside the US rather than implying no insider activity exists.
4. If the symbol is an ETF/mutual fund, call `get_fund_holdings(symbol_id, market)` instead for
   top holdings + sector weightings.
5. If the caller additionally wants a sector- or industry-level view, call
   `get_sector_overview(market, sector_key)` and/or `get_industry_overview(market,
   industry_key)` — both require the caller (or your own resolution via `get_symbol_search`/
   `get_symbol_lookup` from a reference skill) to supply the Yahoo sector/industry key; never
   guess one.
6. If the caller wants upcoming earnings/IPO/economic events for the region, call
   `get_calendar_events_feed(market)` — a market-wide feed bundle, not a single-symbol
   calendar.
7. If the caller specifically wants FinMind's own roster+price dataset for this symbol (a
   distinct source from the yfinance-primary path used above — `id` here is the market's own
   quote symbol, e.g. `^DJI`/`BP.L`/`AALB.AS`/`7203.T`), call `get_international_stock(id,
   market)`; if it's a US symbol and minute-level price is needed, call
   `get_us_stock_price_minute(symbol_id, market="US", start_date, end_date)` (Sponsor-tier).
8. Synthesize using `output_structure`, in order, reporting only sections the tools actually
   returned data for. Never merge `get_international_stock`'s FinMind-sourced numbers into the
   same figure as the yfinance-primary price without labeling which source produced which
   number.
