---
name: market-rankings
version: 1.0.0
description: 全市場排行榜 — 台股法人連買賣／融資增減／殖利率／營收成長排行、美股熱門／市值／營收／ROE／成交量排行、漲跌幅排行、大戶增持排行、個股熱力圖與策略盯盤清單。描述性清單，絕非推薦名單。
triggers: [排行, 排行榜, 漲幅排行, 跌幅排行, 漲跌幅榜, 外資連買, 投信連買, 主力連買, 融資增加, 殖利率排行, 營收成長排行, 熱門美股, 美股市值排行, 大戶增持, 千張大戶, 策略盯盤, 熱力圖, top gainers, top losers, ranking, most active, holder accumulation, strategy watch]
design_pattern: tool_wrapper
inputs:
  kind:
    type: string
    required: false
  market:
    type: string
    required: false
  top_n:
    type: integer
    required: false
tools: [get_tw_market_rankings, get_us_market_rankings, get_price_momentum, get_holder_accumulation, get_stock_heatmap, get_strategy_watch]
output_structure:
  - '排行條件: 市場、排行種類 (kind)、期間或交易日、top_n；讓讀者知道這份清單是怎麼排出來的'
  - '排行清單: 逐檔列出名次與該排行所用的數值 (連買賣天數、增減、漲跌幅、殖利率、成長率等)，保持工具回傳的單位'
  - '大戶增持排行 (Sponsor): 所選持股級距 (張數)、比較的週數與增持幅度'
  - '策略盯盤: 當日各策略入選名單與觸發條件、目前哪些條件成立 (描述性，非訊號)'
  - '資料時效: 每份清單標明資料日期；美股排行為即時查詢，台股排行與漲跌幅為已入庫的收盤後資料'
freshness_rule: 台股排行、漲跌幅榜與大戶增持讀取已入庫的整市場分區（收盤後批次，大戶增持為集保週資料，依週快照而非日資料），必須標明資料日期；美股排行為即時查詢 (top_n 有上限)；策略盯盤為早上 08:00 選出的清單加上最近一次盤中檢查，須標明檢查時間。
boundaries:
  - 排行榜是描述性排序，不是推薦名單；不得說成「值得買」「強勢股該追」，也不得推論排行靠前的股票後續表現。
  - 連買賣、融資增減、增持幅度等籌碼數字只描述已發生的變化，不推論主力動機。
  - get_holder_accumulation 為 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明；集保資料以週快照計算，不得說成每日資料。
  - 策略盯盤的入選名單與觸發條件是條件篩選的結果，不是買賣訊號、進出場價位或目標價。
  - 這些工具各自只回傳有上限的 top-N，不得為了拼湊全市場排名而重複呼叫；工具以 symbol 篩選時也只回答該檔的名次。
  - 美股排行為即時查詢，缺料或查詢失敗時照實說明，不得以台股資料替代。
  - 所有判斷僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Market rankings (descriptive lists, non-trading)

## Procedure

1. Map the question to one tool:
   - TW 籌碼/基本面排行 (自營／投信／外資連買連賣、融資增減、殖利率、月營收成長) →
     `get_tw_market_rankings(kind, top_n, symbol)`; pass `symbol` only to ask where one stock
     ranks.
   - US 排行 (熱門、市值、營收、ROE、成交量) → `get_us_market_rankings(kind, top_n)`.
   - 漲幅／跌幅榜 → `get_price_momentum(market, period, trade_date, top_n)` with `period` of
     day, week or month (defaults to week).
   - 大戶增持 → `get_holder_accumulation(market, threshold, window, as_of, top_n)` (Sponsor);
     `threshold` is the holder tier in 張 (200/400/800/1000), `window` counts weekly snapshots.
   - 個股熱力圖數據 (誰漲得多、成交值大) → `get_stock_heatmap(market, trade_date, period,
     size_by)`.
   - 今日策略盯盤 → `get_strategy_watch(as_of, strategy)`; `strategy` is optional and limited to
     the five known strategies (波動壓縮、超跌反彈、騰落背離、籌碼吸籌、動能旗形).
2. State the ranking conditions first, then the list, then the data date. Keep units as the tool
   returns them; never rank-order again or re-sort in a way that changes meaning.
3. If a list is empty, relay the tool's own reason (the batch has not produced that day yet, no
   data for the kind) rather than implying nothing qualifies.
4. Present every list as a descriptive ranking only. Do not turn it into a "buy list", and do not
   read intent into flows.
