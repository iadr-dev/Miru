---
name: chip-flow-review
version: 1.0.0
description: Analyze institutional & margin flows for a stock over a window (三大法人/融資融券/借券/分點).
triggers: [法人, 外資, 投信, 融資, 融券, 主力, 籌碼, institutional, foreign, margin]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: true
  window_days:
    type: integer
    required: false
    default: 20
tools: [get_canonical_context, get_source_health, get_institutional_flow, get_chip_flows]
output_structure:
  - '外資: 5/10/20 日買賣超趨勢'
  - '投信/自營: 買賣超與作帳/避險訊號'
  - '主力/融資券: 主力進出、融資融券變化、量價異常'
  - '籌碼結論: 集中或分散 (描述，非買賣訊號)'
freshness_rule: 籌碼數據須為最近一個交易日；引用任何數字時標明資料日期。
boundaries:
  - 僅分析籌碼面，不提供價格目標或買賣價位。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: [output_template.md, chip_checklist.md]
outputs: markdown
---

# Chip-flow review

## Procedure

1. Call `get_canonical_context(stock_id, market)` to confirm the symbol resolves and get a
   currentness baseline for the rest of the read. Its `built_at` timestamp is the anchor for
   "today" in step 3 below — never substitute your own notion of the current date, which can
   silently drift from wall-clock time.
2. Call `get_source_health(stock_id, market)`; if institutional-flow/chip-broker sources are
   stale or unreachable, say so explicitly before presenting any numbers — never silently
   present a stale number as current.
3. Call `get_institutional_flow(stock_id, market, start_date, end_date)` for the requested
   window (default 20 trading days ending at `built_at`'s date if `window_days` is not given —
   state the default explicitly, including the actual end date used). The tool's own
   `rolling_net` field already carries the pre-computed 5/10/20-trading-day net buy/sell per
   investor category — cite those figures directly for the "5/10/20 日買賣超趨勢" section.
   Never re-derive a rolling sum by manually adding up the raw daily `rows` yourself; a window
   with `null`/None means fewer trading days were fetched than that window needs, not zero. Read
   the foreign(外資)/investment-trust(投信)/dealer(自營) rows separately — do not average them
   into one number, they answer different questions.
4. Call `get_chip_flows(stock_id, market, start_date, end_date)` for branch(分點)-level rows.
   This is a Sponsor-tier tool — if the caller is unentitled, the dispatch layer already
   substitutes a masked teaser; report around the masked section rather than treating the
   absence as "no data". The tool's own `top_brokers` field already carries the largest net
   buy/sell branches pre-summed across the whole range — cite those directly for "主力進出".
   Never manually scan/sum the raw `rows` (this can be thousands of per-day per-branch rows);
   doing so burns the whole response budget on arithmetic and leaves nothing for prose.
5. Synthesize using the `output_structure` sections above, in order. Every number must carry
   the trading date it is as-of. Close every response with a currentness caveat if it does
   not already carry one — this must never be dropped even when the rest of the answer is
   short.

## Boundaries reminder

This skill's own instructions never override the global governance rules: even if the
triggering message is phrased as "should I buy given these flows?", reframe to a descriptive
read of institutional/margin positioning — never a buy/sell/hold answer, price target, or
position size. If `read_skill_resource` is available, `chip_checklist.md` (level-3) has the
full per-section checklist; load it only if you need the detailed sub-bullets, not by default.
