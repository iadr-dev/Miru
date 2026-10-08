---
name: watchlist-proposal
version: 3.0.0
description: 自選股提案 - turn watchlist requests (add / remove / move stocks, rename / delete / reorder a group) into a confirmable card. Proposes only; the user confirms on the card.
triggers: [加入自選, 加入自選股, 加到自選, 加到自選股, 加進自選, 加進自選股, 放進自選, 放進自選股, 加入我的自選, 移出自選, 移出自選股, 移除自選, 移除自選股, 從自選股移除, 從自選移除, 自選股移除, 自選股刪除, 刪除自選, 刪除自選股, 移到群組, 移到分組, 搬到群組, 群組, 分組, 自選股群組, 自選股分組, 群組改名, 分組改名, 刪掉群組, 刪除群組, 刪除分組, 排到最前面, 排到最後面, add to my watchlist, add to watchlist, to my watchlist, remove from my watchlist, remove from watchlist, from my watchlist, my watchlist, watchlist group, rename group, delete group, watchlist add]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: true
  market:
    type: string
    required: false
tools: [propose_watchlist_change, get_symbol_directory_search, ask_user]
output_structure:
  - '一張提案卡：動作、每檔標的的代號與名稱、要放入的群組（可選現有群組或新增群組），或要改名／刪除／調整順序的群組'
  - '僅一句話說明：提案已顯示在畫面上，需由使用者確認並按下按鈕才會生效（不列點、不加 TL;DR、不重述卡片內容）'
freshness_rule: 提案本身不讀取任何行情資料；自選股只是使用者自己的關注清單。
boundaries:
  - 此技能只能「提議」，絕不自行加入或移除；變更只有在使用者於提案卡上按下按鈕後才會發生。回覆時不得說「已幫你加入／移除」，只能說「已列出提案，請確認」。
  - 使用者只說公司名稱時，先用 get_symbol_directory_search 查出代號；不得憑記憶猜代號。名稱有多個可能時用 ask_user 問。
  - 同一次請求的所有標的放在同一次 propose_watchlist_change；一次最多 10 檔，同一市場。
  - 搬移（move）要帶現有的目標群組名稱；群組管理（rename_group / delete_group / reorder_group）帶群組名稱，不帶 symbols。
  - 刪除群組是破壞性操作，只能提議；卡片會顯示群組內有幾檔標的，等使用者確認。
  - 使用者指定群組名稱時帶入 group_name（不存在會在確認時建立）；沒指定就不要填，讓使用者在卡片上選。
  - 加入自選股不是推薦或買賣訊號；不得暗示這些標的值得買進。
resources: []
outputs: markdown
---

# 自選股提案

使用者要調整自選股（加入、移除、搬到別的群組，或群組改名、刪除、調整順序）時，用 `propose_watchlist_change` 擬一張提案卡，讓使用者自己確認並按下按鈕。

1. 只有公司名稱時，先用 `get_symbol_directory_search` 查代號並確認是正確的那一檔；不確定就用 `ask_user`。
2. 把這次請求的所有標的一次放進 `propose_watchlist_change`（`action` 為 `add`、`remove`、`move`、`rename_group`、`delete_group` 或 `reorder_group`）。
3. 用一句話說明提案已顯示、需按下按鈕才會生效。
