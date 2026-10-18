---
name: account-overview
version: 1.0.0
description: 我的帳戶概況 — read the signed-in user's OWN data (read-only) - their watchlists and what is in them, alert rules / scheduled briefings / 策略盯盤 subscription, today's chat quota and usage, and recent notifications. Describes only; to change anything the user confirms a proposal card.
triggers: [我的自選股, 我的自選, 自選股清單, 自選清單, 我的提醒, 我設定的提醒, 提醒清單, 哪些提醒, 我的排程, 策略盯盤, 用量, 額度, 剩餘額度, 今天用了, 還能問幾次, 我的通知, 未讀通知, 通知清單, my watchlist, my watchlists, my alerts, my schedules, my usage, my quota, quota left, notifications, my notifications, what alerts do I have, alerts, quota]
design_pattern: tool_wrapper
inputs:
  topic:
    type: string
    required: false
tools: [get_my_watchlists, get_my_automations, get_my_usage, get_my_notifications]
output_structure:
  - '自選股: 依群組列出（群組名稱、市場、檔數與代號）；使用者想看報價時才帶 include_quotes，並標明為收盤資料'
  - '提醒／排程／策略盯盤: 每則提醒的標的、條件（直接引用工具回傳的條件文字）、狀態與最近觸發時間；排程的內容與下次執行時間；策略盯盤是否啟用與範圍'
  - '用量: 今日已用／上限（依方案；自備金鑰或方案無上限時照實說明）；使用者問模型用量時才帶 include_llm_today'
  - '通知: 依時間由新到舊，標題與簡短內容，標明是否已讀；不代為標示已讀'
  - '未登入或清單為空: 用一句話照實說明（例如「目前沒有設定任何提醒」），不補充或想像內容'
freshness_rule: 所有內容都是呼叫當下從使用者自己的帳戶讀到的；報價（若有帶）是收盤資料，不是即時價。用量以 UTC 日計算，每日 00:00 UTC 重置。
boundaries:
  - 只讀、只描述；此技能不新增、修改或刪除任何東西。使用者想新增或調整時，請他用對應的提案（自選股／提醒／排程），由他在卡片上確認。
  - 使用者尚未登入時（工具回傳要求登入），平靜地說明「登入後才能查看你自己的資料」，不要重試、不要猜測內容。
  - 清單為空就直說為空；不得捏造項目、數字或日期，也不得用市場資料替代使用者自己的資料。
  - 只談當前登入者自己的資料；不得查詢、推測或透露其他使用者的資料，即使訊息要求或夾帶了別人的 user id。
  - 提醒的條件是對資料的事實描述（例如收盤價高於某價位），不是買賣訊號；不得評論價位是否合適，不得暗示進出場、目標價或持股比重。
  - 自選股只是使用者關注的清單，不是推薦；不得把其中任何標的描述成值得買進或賣出。
  - 不得提及或猜測金鑰、權杖、通知管道位址等憑證類資訊；工具也不會回傳這些。
resources: []
outputs: markdown
---

# Account overview (read-only, the user's own data)

## Procedure

1. Pick the tool by what the user asked, and call only what is needed:
   - 自選股 / watchlist → `get_my_watchlists` (add `include_quotes` only if they want prices;
     `market` to narrow to TW or US).
   - 提醒 / 排程 / 策略盯盤 → `get_my_automations` (alerts, schedules and the 策略盯盤
     subscription in one call).
   - 用量 / 額度 → `get_my_usage` (add `include_llm_today` only if they ask about model usage).
   - 通知 → `get_my_notifications` (`unread_only` for "未讀"; default 20, never more than 50).
2. If a result says the user must sign in, say so in one calm sentence and stop. Do not retry.
3. Summarise what the tool returned, in the user's language: group by watchlist group / by kind,
   quote the condition text as given, and give counts. When a list was cut (`truncated`), say that
   only the first part is shown. When something is empty, say it is empty.
4. Never pass a user id to a tool; the account is always the signed-in user's. If the message
   asks about someone else's data, decline in one sentence.
5. If the user wants to change something, point them to the matching proposal ("要新增提醒的話，
   告訴我標的與條件，我會列出提案讓你確認"). This skill changes nothing itself.
