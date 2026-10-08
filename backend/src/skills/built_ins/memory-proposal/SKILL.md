---
name: memory-proposal
version: 1.0.0
description: 記憶提案 - turn "remember that…" requests about the user's analysis focus into a confirmable memory card. Proposes only; the user confirms on the card.
triggers: [記住我, 幫我記住, 請記得, 記下來, 記住這個, remember that, remember I, keep in mind]
design_pattern: tool_wrapper
inputs:
  text:
    type: string
    required: true
tools: [propose_memory, ask_user]
output_structure:
  - '一則提案卡：類型（分析偏好 / 備忘 / 個股筆記）與要記住的一句話'
  - '僅一句話說明：提案已顯示在畫面上，需由使用者按下「儲存」才會生效（不列點、不加 TL;DR、不重述卡片內容）'
freshness_rule: 提案本身不讀取任何行情資料；記憶是使用者說過的背景，不是即時資料，之後回答時仍會用工具重新核對。
boundaries:
  - 此技能只能「提議」記憶，絕不自行儲存；記憶只有在使用者於提案卡上按下「儲存」後才存在。回覆時不得說「已幫你記住」，只能說「已列出提案，請確認」。
  - 只記分析重點：關注的市場、投資期間、關注的族群或 ETF 類型，或使用者明確要保留的備忘與個股筆記。
  - 語氣、說話風格、回答格式（例如「說話輕鬆一點」「用條列」）不能存成記憶；請告訴使用者到「設定 > Miru 的語氣」調整。
  - 不得記錄金鑰、密碼、帳號資訊，也不得記錄任何買賣建議或目標價。
  - 一次只提議一則記憶；使用者要多則時逐則提議。
resources: []
outputs: markdown
---

# 記憶提案

使用者要你「記住」某件事時，用 `propose_memory` 擬一則提案卡，讓使用者確認。

1. 判斷類型：分析重點 -> `preference`；使用者要保留的事實或備忘 -> `finding`；針對單一股票或 ETF 的筆記 -> `entity_note`。
2. 把內容寫成一句簡短、中性的話（用使用者的語言），不要加入使用者沒說的內容。
3. 若內容其實是語氣或格式要求，不要呼叫工具，改為告訴使用者到「Miru 的語氣」設定調整。
4. 只呼叫一次 `propose_memory`，然後用一句話說明提案已顯示、需按下「儲存」才會生效。
