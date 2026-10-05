---
name: schedule-proposal
version: 1.0.0
description: 排程提案 — turn "brief me every day/week…" requests into a confirmable recurring-briefing card for Taiwan/US stocks or an ETF. Proposes only; the user confirms on the card.
triggers: [每天, 每日, 每週, 每周, 每月, 每個交易日, 每天開盤前, 每天收盤後, 每天早上, 每週一, 每週五, 每日簡報, 定期, 固定時間, 排程, 週報, 日報, 天天, 幫我定時, daily briefing, weekly report, recurring, schedule a brief, every day, every morning, every week, 每天幫我, 每日幫我, 每週幫我, 每周幫我, 每月幫我, 每天固定, 每週固定, 定期幫我, 定期寄給我, 定期產生]
design_pattern: tool_wrapper
inputs:
  template:
    type: string
    required: true
  targets:
    type: array
    required: true
tools: [propose_schedule, ask_user]
output_structure:
  - '一則提案卡：簡報種類、標的、執行時間'
  - '僅一句話說明：提案已顯示在畫面上，需由使用者按下「建立」才會生效（不列點、不加 TL;DR、不重述卡片內容）'
freshness_rule: 排程簡報在執行當下才即時產生，提案本身不讀取任何行情資料。
boundaries:
  - 此技能只能「提議」排程，絕不自行建立；排程只有在使用者於提案卡上按下「建立」後才存在。回覆時不得說「已幫你設定/已建立」，只能說「已列出提案，請確認」。
  - 只能從既有的簡報種類中挑選，不得自創時間或技能；使用者要求的時間若不在種類內，說明可選的時間並請使用者選擇。
  - 標的須由使用者的話推出；沒給標的時先用 ask_user 問，不得自行挑選股票。
  - 簡報是資料整理，不是買賣訊號；不得暗示任何進出場建議。
  - 一次只提議一個排程；使用者要多個時逐個提議。
resources: []
outputs: markdown
---

# Schedule proposal (propose-only, non-trading)

## Procedure

1. Identify what the user wants briefed and on what cadence. If the stock or the cadence is
   unclear, call `ask_user` and stop for this turn.
2. Map the wording to exactly one `template`:
   - 每天開盤前的簡短快訊 (one or more Taiwan stocks) → `pre_open_brief`
   - 每天收盤後的籌碼/法人檢視 (one Taiwan stock) → `after_close_chip`
   - 每天收盤後的注意股/處置股檢查 (one Taiwan stock) → `disposition_watch`
   - 每週一的公告與內部人動態掃描 (one Taiwan stock) → `weekly_catalyst`
   - 完整分析報告（五年財務表、圖表、季節性、風險；每週一份，一次一檔）：台股個股 → `tw_stock_report`、美股個股 →
     `us_stock_report`、台股 ETF → `tw_etf_report`、美股 ETF → `us_etf_report`
3. Call `propose_schedule` once with the `template` and the ticker `targets`. It shows a
   confirmation card and creates nothing.
4. Reply with exactly ONE short sentence: the proposal is on screen and only exists once the user
   taps 建立. No bullet list, no "TL;DR" or summary line, and do not restate the card's template /
   stock / time — the card already shows them. Never say the schedule "has been set".
