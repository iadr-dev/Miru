---
name: alert-proposal
version: 1.0.0
description: 提醒提案 — turn "notify me when…" requests into a confirmable alert-rule card for one Taiwan/US stock or ETF. Proposes only; the user confirms on the card.
triggers: [提醒我, 通知我, 設定提醒, 到價提醒, 警示, 連買提醒, 處置股提醒, 月營收提醒, 融資提醒, 折溢價提醒, alert me, notify me, set an alert, price alert]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: true
  market:
    type: string
    required: false
  condition:
    type: string
    required: true
tools: [propose_alert, ask_user]
output_structure:
  - '一則提案卡：標的、市場、條件（價位 / 漲跌幅 / 成交量 / 法人連買賣 / 注意股 / 處置股 / 月營收公布 / 融資融券增減 / ETF 折溢價）、通知頻率、有效期'
  - '僅一句話說明：提案已顯示在畫面上，需由使用者按下「建立」才會生效（不列點、不加 TL;DR、不重述卡片內容）'
freshness_rule: 提案本身不讀取任何行情資料；提醒建立後，條件只會在收盤後資料更新時評估，不是即時監看。提案時須如實說明「資料更新後才會比對」，不得暗示即時通知。
boundaries:
  - 此技能只能「提議」提醒，絕不自行建立；提醒只有在使用者於提案卡上按下「建立」後才存在。回覆時不得說「已幫你設定/已建立」，只能說「已列出提案，請確認」。
  - 條件須由使用者的話推出；使用者沒給門檻（價位、幅度、天數）時，先用 ask_user 問，不得自行捏造數字。
  - 提醒是「你設定的資料條件達成時通知」，不是買賣訊號；不得建議該設定什麼價位、不得暗示價位是目標價或進出場點。
  - 注意股、處置股、月營收公布與法人連買賣僅適用台股；美股標的只提供價位、漲跌幅、成交量條件。
  - 一次只提議一則提醒；使用者要多則時逐則提議。
resources: []
outputs: markdown
---

# Alert proposal (propose-only, non-trading)

## Procedure

1. Identify the single symbol and market from the request. If the symbol or market is ambiguous
   (e.g. a name that maps to several instruments), call `ask_user` and stop for this turn.
2. Map the user's wording to exactly one condition kind:
   - 收盤價高於/低於 X → `close_price`
   - 單日漲/跌幅達 X% → `daily_change`
   - 成交量放大 N 倍 → `volume_surge`
   - 外資/投信/自營商連續 N 日買超/賣超 → `institutional_streak` (TW only)
   - 列入注意股 → `attention_entry` (TW only)
   - 列入處置股 → `disposition_entry` (TW only)
   - 公布月營收 → `month_revenue_published` (TW only; it only says the figure was published)
   - 公司公布重大訊息 → `material_announcement` (TW only; it only says a filing exists today, the
     alert quotes the issuer's subject line and never interprets it)
   - 融資/融券餘額單日增減 X% → `margin_balance_change` (TW only)
   - ETF 折價/溢價達 X% → `etf_premium_discount` (TW ETF codes only; the NAV is an issuer estimate)
3. If the user did not state the threshold the condition needs, ask for it with `ask_user`
   instead of choosing a number for them.
4. Call `propose_alert` once. It shows a confirmation card and creates nothing.
5. Reply with exactly ONE short sentence: the proposal is on screen and only exists once the user
   taps 建立 (end-of-day data updates are what it is checked against, not real time). No bullet
   list, no "TL;DR" or summary line, and do not restate the card's symbol / condition / expiry —
   the card already shows them. Never say the alert "has been set".
