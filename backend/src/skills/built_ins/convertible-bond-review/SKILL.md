---
name: convertible-bond-review
version: 1.0.0
description: One convertible bond's profile (roster/overview/put schedule) plus its trading/institutional-flow/custody picture.
triggers: [可轉債, 可轉換公司債, 賣回權, convertible bond, CB]
design_pattern: tool_wrapper
inputs:
  cb_id:
    type: string
    required: true
  start_date:
    type: string
    required: false
  end_date:
    type: string
    required: false
tools: [get_source_health, get_convertible_bond_profile, get_convertible_bond_flow]
output_structure:
  - '債券基本資料: 發行條件、轉換/贖回條款概覽'
  - '賣回權時程: 賣回權排程'
  - '日成交概況: EOD 成交價量'
  - '三大法人流向: 可轉債層級的三大法人買賣'
  - '月保管餘額分析: 月度保管餘額變化'
freshness_rule: 每一區塊須標明其資料日期或期間；若某一子資料集在該時段內無資料，明確說明，不得留白。
boundaries:
  - id 欄位為債券本身的 cb_id，並非對應股票的股票代號 — 不得混用兩者。
  - 僅描述已公告條款與已結算交易/流向資料，不提供轉換價值、套利空間或買賣建議的計算或推薦。
  - 兩個工具皆為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Convertible-bond review

## Procedure

1. Call `get_source_health(cb_id, market)` first — this domain has no dedicated canonical-
   context wrapper, so source health is the currentness baseline here.
2. Call `get_convertible_bond_profile(cb_id, market, start_date, end_date)` (Sponsor-tier) —
   bundles roster info, daily overview (conversion/put/early-redemption terms), and the put
   (賣回權) schedule for one bond. `id` here is the bond's own `cb_id`, never the underlying
   equity's stock id.
3. Call `get_convertible_bond_flow(cb_id, market, start_date, end_date)` (Sponsor-tier) —
   bundles EOD trading, three-major-institutional-investor flow, and monthly custody-balance
   analysis for the same bond. Both CB tools carry `health_status` (the worst status among the
   bundled sources): when it is `unreachable` (or any non-`ok` tier), an empty section means the
   source could not be reached, not that the bond has no data - say so.
4. If the caller is unentitled to either Sponsor tool, report around the masked section rather
   than treating the bond as having no data at all.
5. Synthesize using `output_structure`, in order. Never compute or imply a conversion-value/
   arbitrage read from the terms + price data — describe the terms and the trading history
   only, leave any valuation judgment to the caller.
