---
name: derivatives-positioning
version: 1.0.0
description: 期權 OI / P-C 比 / 大額交易人集中度 positioning context for one futures/option contract code (descriptive, non-trading).
triggers: [期權, 選擇權, 大額交易人, 未平倉, OI, put call, put/call, VIX, 波動率指數, derivatives, options positioning]
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
tools: [get_canonical_context, get_source_health, get_derivatives_institutional, get_put_call_ratio, get_taiex_vix]
output_structure:
  - '三大法人期權日盤淨頭寸: 外資/投信/自營期貨與選擇權方向'
  - '大額交易人集中度: 前五/十大交易人未平倉占比 (Sponsor)'
  - 'P/C 比: 由 call/put 每日成交量自行換算，非現成比率資料集'
  - '波動率: 臺指選擇權 VIX 讀值 (Sponsor)'
  - '綜合觀察: 多空氣氛描述 (非買賣訊號)'
freshness_rule: 每筆數字須標明交易日；三大法人與大額交易人為日盤資料，VIX 與 P/C 量為當日或區間彙總，缺一律標明。
boundaries:
  - 僅描述期權市場籌碼與波動率結構，不提供進場/出場價位或方向性建議。
  - P/C 比是由 get_put_call_ratio 回傳的原始 call/put 成交量自行計算，計算方式須在回覆中說明，不得暗示為官方比率資料集。
  - 大額交易人未平倉與 VIX 屬 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明，不得視為「無資料」。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Derivatives positioning

## Procedure

1. Call `get_canonical_context(contract_id, market)` for a currentness baseline, then
   `get_source_health(contract_id, market)` — surface any stale/unreachable source before
   presenting numbers.
2. Call `get_derivatives_institutional(contract_id, market, start_date, end_date)`. This one
   tool bundles four datasets: day-session three-major-institutional-investor futures flow,
   the same for options, futures large-trader (大額交易人) open-interest concentration, and the
   option-side equivalent. The large-trader OI slice is Sponsor-tier even though the whole
   tool call is one request — if the caller is unentitled, report around the masked section
   rather than treating it as absent data.
3. Call `get_put_call_ratio(contract_id, market, start_date, end_date)` for the raw daily
   call/put volume rows. There is no separate P/C-ratio dataset upstream — this tool returns
   volume only, so compute the ratio yourself (`put_volume / call_volume` per day) and say so
   explicitly; never present it as a pre-built official ratio series.
4. Call `get_taiex_vix(market)` for the TAIEX option volatility index reading (Sponsor-tier).
5. Synthesize using the `output_structure` sections above, in order. Carry each figure's
   trading date. Describe positioning/concentration/volatility as market structure only —
   never translate an OI or VIX reading into a directional call, price target, or timing
   signal.
