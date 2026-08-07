---
name: governance-risk
version: 1.0.0
description: 質押比 (董監事持股) + 資金貸與/質押 collateral-balance red-flag gate for one stock (narrowed — 背書保證/訴訟裁罰 not covered).
triggers: [質押比, 資金貸與, 董監事質押, governance risk, pledge ratio, collateral balance]
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
tools: [get_canonical_context, get_source_health, get_insider_shareholding, get_loan_collateral_balance]
output_structure:
  - '董監事持股質押比: 逐人現況與相對於選任時的變化'
  - '資金貸與/質押餘額: 區間內餘額變化 (Sponsor)'
  - '風險旗標判讀: 質押比與餘額走勢的描述性風險說明 (非買賣訊號)'
  - '尚無法提供的風險面: 明確列出 (背書保證/訴訟裁罰)'
freshness_rule: 董監事持股為當月餘額；資金貸與/質押餘額須標明區間起訖日；兩者缺一律標明資料截止時間。
boundaries:
  - 本技能範疇窄於原始設計：背書保證、訴訟裁罰目前無對應工具，一律在 missing_scope 中列出，不得杜撰或用其他欄位替代。
  - get_loan_collateral_balance 回傳的是餘額數字，非「質押比/背書保證/訴訟裁罰」綜合治理風險分數 — 不得暗示為綜合評分。
  - 僅描述風險旗標，不提供買賣或持股調整建議。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Governance risk (narrow, honest version)

## Procedure

1. Call `get_canonical_context(stock_id, market)` for a currentness baseline, then
   `get_source_health(stock_id, market)` — surface any stale/unreachable source first.
2. Call `get_insider_shareholding(stock_id, market)` for the current-month per-person 董監事
   持股餘額 rows, reading the pledged-shares ratio (質押比) field specifically. Compare each
   person's current ratio to their at-election baseline when both are present; never invent a
   trend from a single snapshot.
3. Call `get_loan_collateral_balance(stock_id, market, start_date, end_date)` (Sponsor-tier)
   for the 資金貸與/質押 collateral-balance rows over the requested window. This is a **balance
   figure**, not a composite governance-risk score — report it as a balance trend, and if the
   caller is unentitled, report around the masked section rather than treating it as absent.
4. Explicitly disclose, in a "尚無法提供" section, that 背書保證 (endorsement/guarantee
   exposure) and 訴訟裁罰 (litigation/penalty records) have no adapter today — never let the
   質押比/資金貸與 read stand in for a full governance-risk composite.
5. Synthesize using `output_structure`, in order, describing risk flags only (e.g. "質押比偏高
   且上升" is a descriptive observation, never a recommendation to act on it).
