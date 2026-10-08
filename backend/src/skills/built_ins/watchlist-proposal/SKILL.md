---
name: watchlist-proposal
version: 1.0.0
description: 自選股提案 - turn "add X to my watchlist" requests into a confirmable card for one Taiwan/US stock or ETF. Proposes only; the user picks the group and confirms on the card.
triggers: [加入自選, 加入自選股, 加到自選, 加到自選股, 加進自選, 加進自選股, 放進自選, 放進自選股, 加入我的自選, add to my watchlist, add to watchlist, to my watchlist, my watchlist, watchlist add]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: true
  market:
    type: string
    required: false
tools: [propose_watchlist_add, ask_user]
output_structure:
  - '一則提案卡：標的代號、市場，讓使用者選擇要加入的自選股群組'
  - '僅一句話說明：提案已顯示在畫面上，需由使用者選擇群組並按下「加入」才會生效（不列點、不加 TL;DR、不重述卡片內容）'
freshness_rule: 提案本身不讀取任何行情資料；自選股只是使用者自己的關注清單。
boundaries:
  - 此技能只能「提議」，絕不自行加入；標的只有在使用者於提案卡上按下「加入」後才會進入自選股。回覆時不得說「已幫你加入」，只能說「已列出提案，請確認」。
  - 代號不明確時先用 ask_user 問，不得自行猜測。
  - 加入自選股不是推薦或買賣訊號；不得暗示這檔值得買進。
  - 一次只提議一檔標的；使用者要多檔時逐檔提議。
resources: []
outputs: markdown
---

# 自選股提案

使用者要把某檔台股或美股加入自選股時，用 `propose_watchlist_add` 擬一張提案卡，讓使用者自己選群組並確認。

1. 從使用者的話找出代號與市場；代號不確定就用 `ask_user`。
2. 只呼叫一次 `propose_watchlist_add`。
3. 用一句話說明提案已顯示、需按下「加入」才會生效。
