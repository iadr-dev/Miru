---
name: warrant-and-derivatives-microstructure
version: 1.0.0
description: Per-trade tick history for one futures/option/spread contract, a bounded intraday options snapshot, one warrant's branch flow, plus the night-session three-major-institutional futures/option flow (all Sponsor-tier, all one trading day/one snapshot at a time).
triggers: [權證分點, 期貨逐筆, 選擇權逐筆, 價差逐筆, 選擇權快照, 夜盤法人, 盤後法人, warrant broker flow, futures tick, option tick, options snapshot, night session institutional]
design_pattern: tool_wrapper
inputs:
  contract_id:
    type: string
    required: true
  trade_date:
    type: string
    required: false
tools: [get_source_health, get_futures_tick, get_futures_spread_tick, get_option_tick, get_options_snapshot, get_derivatives_institutional_after_hours, get_warrant_chip_broker]
output_structure:
  - '期貨逐筆成交: 單一交易日的逐筆成交紀錄 (Sponsor)'
  - '價差逐筆成交: 單一交易日的期貨跨月價差逐筆成交紀錄 (Sponsor)'
  - '選擇權逐筆成交: 單一交易日的逐筆成交紀錄 (Sponsor)'
  - '選擇權快照: 單次有界盤中觀測 — 價格/成交量/最佳買賣價 (Sponsor)'
  - '夜盤三大法人: 期貨/選擇權夜盤買賣方向 (Sponsor，無未平倉拆分)'
  - '權證分點進出: 單一交易日的券商買賣量價 (Sponsor)'
freshness_rule: 每組逐筆/分點資料僅服務單一交易日；若使用者要求區間，逐日重複呼叫並明確說明這是多次單日查詢的彙整，而非一次區間查詢。
boundaries:
  - 所有工具皆為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明，不得視為「無資料」。
  - get_futures_tick/get_futures_spread_tick/get_option_tick 皆為單一交易日、逐筆成交紀錄 (非日成交彙總)，量為雙邊計算 — 不得與 futures-options-market-data 技能的日成交/未平倉彙總混用。
  - get_options_snapshot 為單次有界盤中觀測，不建立輪詢或串流；每次呼叫都是一次獨立、有界的查詢，與 live-snapshot-check 技能的個股快照是不同標的類型 (契約代碼而非股票代碼)。
  - get_derivatives_institutional_after_hours 是夜盤 (night session) 專用，且無未平倉拆分 — 與 derivatives-positioning 技能的日盤三大法人資料是不同資料集，不得互相取代或合併呈現。
  - get_warrant_chip_broker 是權證分點資料，與 chip-flow-review 技能的個股分點 (get_chip_flows) 及 block-trade-and-broker-flow 技能的個股分點彙總 (get_chip_broker_agg) 皆為不同資料集，權證代碼與股票代碼不可互換輸入。
  - 逐筆成交紀錄資料量可能很大，不需將全部原始列逐一複誦；改以量價區間、成交筆數、方向分布等摘要方式呈現。
  - 所有判斷僅為資訊參考，不構成投資建議，不提供任何進出場時點建議。
resources: []
outputs: markdown
---

# Warrant and derivatives microstructure

## Procedure

1. Call `get_source_health(contract_id, market)` as a currentness baseline before presenting
   any figures.
2. If the caller wants futures per-trade detail, call `get_futures_tick(contract_id, market,
   trade_date)` — one trading day per request; if a range is wanted, call once per day and say
   so explicitly.
3. If the caller wants a futures calendar-spread product's per-trade detail, call
   `get_futures_spread_tick(contract_id, market, trade_date)` — same one-day-per-request shape.
4. If the caller wants an option contract's per-trade detail, call `get_option_tick(contract_id,
   market, trade_date)` — same one-day-per-request shape.
5. If the caller wants a single bounded intraday check of an option contract's current price/
   volume/best bid-ask, call `get_options_snapshot(contract_id, market)` instead — one explicit
   observation, never a poll or a stream; repeat calls each count as a new explicit check.
6. If the caller wants night-session (夜盤) institutional flow for a futures/option contract,
   call `get_derivatives_institutional_after_hours(contract_id, market, trade_date)` — note
   explicitly that this dataset carries no open-interest-balance breakdown, unlike the
   day-session bundle used elsewhere.
7. If the caller has a warrant code and wants branch/broker-level buy-sell flow, call
   `get_warrant_chip_broker(warrant_id, market, trade_date)` — single trading day per request
   upstream; never pass `end_date`.
8. Because every tool here returns per-trade/per-broker row-level data, summarize by volume/
   price range, trade count, and net buy/sell direction rather than reproducing every row —
   reserve full-row detail only for a caller who explicitly asks to see individual trades.
9. Synthesize using `output_structure`, reporting only the section(s) the query actually needs.
