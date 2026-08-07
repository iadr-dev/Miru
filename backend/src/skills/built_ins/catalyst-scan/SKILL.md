---
name: catalyst-scan
version: 1.0.0
description: MOPS 重大訊息 + 董監事持股/質押 catalysts for one stock today (narrowed to what's actually backed; 董事會決議/庫藏股/法說會/自結 not covered).
triggers: [重大訊息, 內部人, 董監事持股, 質押, catalyst, material announcement, insider shareholding]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: true
tools: [get_canonical_context, get_source_health, get_material_announcements, get_insider_shareholding]
output_structure:
  - '今日重大訊息: MOPS 當日公告列表 (契約/併購/訴訟/財測/澄清等類型)'
  - '董監事持股變化: 本月持股餘額與質押比 (風險旗標)'
  - '尚無法提供的催化劑類型: 明確列出 (董事會決議/庫藏股/法說會/自結 等)'
freshness_rule: 重大訊息僅為當日快照 (無日期區間參數)，董監事持股為當月餘額；兩者皆須標明資料截止日。
boundaries:
  - 本技能範疇窄於原始設計：董事會決議、庫藏股、法說會、自結財報目前無對應工具，一律列在 missing_scope，不得杜撰。
  - 不解讀公告內容為買賣訊號，僅描述事件類型與內容摘要。
  - 質押比升高僅作為風險描述性旗標，不構成投資建議。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Catalyst scan (narrow, honest version)

## Procedure

1. Call `get_canonical_context(stock_id, market)` for a currentness baseline, then
   `get_source_health(stock_id, market)` — surface any stale/unreachable source first.
2. Call `get_material_announcements(stock_id, market)`. This is MOPS's **current-day dump**
   only — there is no date-range parameter upstream, so never imply a historical catalyst
   timeline from this call; it is "what MOPS has published today for this stock."
3. Call `get_insider_shareholding(stock_id, market)` for the current-month 董監事持股餘額 rows,
   including each person's pledged-shares ratio (質押比). Treat a high/rising 質押比 as a
   descriptive risk flag only — never frame it as a sell signal.
4. Explicitly list, in a "尚無法提供" section, the catalyst types this skill's real tools do
   not cover: 董事會決議, 庫藏股實施, 法人說明會排程, 自結財報 — no adapter exists for any of
   these today (no matching MOPS capability is wired). Never present today's announcement dump
   as if it were a complete catalyst calendar.
5. Synthesize using `output_structure`, in order, every item tagged with its as-of date.
