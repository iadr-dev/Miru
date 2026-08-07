---
name: futures-options-market-data
version: 1.0.0
description: 期貨/選擇權 contract roster, EOD price/volume/OI, dealer volume, spread quotes, final settlement, and TAIFEX official settlement for one contract code.
triggers: [期貨, 選擇權合約, 結算價, 價差交易, 自營商, futures, options contract, settlement price]
design_pattern: tool_wrapper
inputs:
  contract_id:
    type: string
    required: true
  start_date:
    type: string
    required: false
  end_date:
    type: string
    required: false
tools: [get_canonical_context, get_source_health, get_futopt_roster, get_futures_options_daily, get_dealer_volume, get_futures_spread_trading, get_final_settlement_price, get_derivatives_settlement, get_asset_swap_daily]
output_structure:
  - '合約基本資料: 若需要，從全市場合約代碼參考表中找出對應合約 (參考用)'
  - '日成交/未平倉: 期貨與選擇權 EOD 價量與未平倉'
  - '自營商成交量: 期貨/選擇權每一自營商成交量 (參考用，非法人流)'
  - '價差交易報價: 期貨跨月價差 (Sponsor)'
  - '最後結算價: FinMind 期貨/選擇權最後結算價 (Sponsor)，以及 TAIFEX 官方每日結算價/量/未平倉'
  - '資產交換: 資產交換固定收益與選擇權日成交 (Sponsor，通常用於個股資產交換相關查詢)'
freshness_rule: 每一筆數字須標明交易日；結算相關數字須區分「FinMind 最後結算價 (get_final_settlement_price)」與「TAIFEX 官方每日結算 (get_derivatives_settlement)」兩個不同來源，不得混稱。
boundaries:
  - 本技能僅描述期貨/選擇權市場結構資料 (價量/未平倉/結算/自營商量)，不提供任何方向性交易建議、進出場時點或槓桿操作建議。
  - 自營商成交量 (get_dealer_volume) 是交易量統計，不等於三大法人期權流向 (該讀取請改用 derivatives-positioning 技能)。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Futures & options market data

## Procedure

1. Call `get_canonical_context(contract_id, market)` for a currentness baseline, then
   `get_source_health(contract_id, market)` — surface any stale/unreachable source first.
2. If the caller needs to resolve or verify a contract code, call `get_futopt_roster(market)`
   once — the whole-market futures/options contract-code reference roster (market-level, never
   per-contract).
3. Call `get_futures_options_daily(contract_id, market, start_date, end_date)` for EOD price/
   volume/open-interest — note one of `futures`/`options` will typically be empty since a
   contract code is either a futures or an option code in practice; that's expected, not an
   error.
4. Call `get_dealer_volume(contract_id, market, start_date, end_date)` for per-dealer trading
   volume (futures + options) — a trading-volume statistic, not an institutional-flow signal;
   never conflate with `get_derivatives_institutional`'s three-major-institutional read (that
   belongs to the `derivatives-positioning` skill).
5. Call `get_futures_spread_trading(contract_id, market, start_date, end_date)` (Sponsor-tier)
   for calendar-spread quotes.
6. Call `get_final_settlement_price(contract_id, market, start_date, end_date)` (Sponsor-tier,
   FinMind) and, separately, `get_derivatives_settlement(contract_id, market)` (public, TAIFEX
   OpenAPI's own official daily settlement) — label each result with its actual source so the
   two settlement figures are never merged into one unattributed number.
7. If relevant, call `get_asset_swap_daily(contract_id, market, start_date, end_date)`
   (Sponsor-tier) for 資產交換 fixed-income/option daily trading.
8. Synthesize using `output_structure`, in order, reporting around any masked Sponsor section.
