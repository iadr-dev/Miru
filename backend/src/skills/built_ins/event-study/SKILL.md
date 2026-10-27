---
name: event-study
version: 1.0.0
description: 事件後歷史統計 — 過去個股在處置起日/出關、列入注意股、減資恢復買賣、庫藏股買回起訖之後的 5/20/60 個交易日實際漲跌分布 (件數、平均/中位數、相對市場中位數的超額報酬、逐年表)，純描述性歷史統計，附樣本數與限制，不是訊號或預測。
triggers: [處置後, 處置股後, 處置之後, 被處置後, 處置期間結束後, 出關後, 出關之後, 解除處置後, 注意股後, 列入注意股後, 被列注意股後, 減資後, 減資恢復買賣後, 恢復買賣後, 庫藏股後, 買回庫藏股後, 庫藏股買回後, 事件研究, 事件後, 事件後表現, 事件後的表現, 歷史表現, 歷史上, 歷史統計, 之後的表現, 後續表現, 注意股之後, 減資之後, 庫藏股之後, 復牌後, 恢復買賣之後, 處置股出關, 個交易日, 平均漲跌, 平均報酬, 平均表現, event study, disposition release, post-disposition, historical statistics, average return after, after disposition, after being disposed, after release from disposition, after attention list, after capital reduction, after buyback, after treasury stock, historically after]
design_pattern: tool_wrapper
inputs:
  event_type:
    type: string
    required: true
  stock_id:
    type: string
    required: false
  start_date:
    type: string
    required: false
  end_date:
    type: string
    required: false
tools: [get_canonical_context, get_source_health, get_event_study]
output_structure:
  - '事件定義與資料範圍: 事件的白話定義、第 0 日定義、事件資料實際涵蓋的起訖日 (event_feed_first_snapshot 起) 與價格最新日'
  - '各觀察期統計: 5/20/60 個交易日各自的事件件數 n、不同個股數、平均/中位數/標準差/四分位、上漲占比，以及相對市場中位數的平均/中位超額報酬；n 不足的觀察期只列件數並說明「樣本不足，不提供統計數字」'
  - '事前 5 日漲跌 (背景資訊) 與逐年表: 說明事件集中在特定日期，實際獨立樣本比 n 少 (event_clustering)'
  - '該股自己的過去事件 (使用者指定個股時): 逐筆事件日期與各期漲跌，並說明單檔樣本很小'
  - '限制: 逐項列出 limitations (價格資料約三年、事件資料只向前累積、集中度、倖存者偏差、未還原股利、未計成本、過去不代表未來)'
freshness_rule: 價格為收盤後入庫資料，須標明 latest_price_date；事件資料是每日快照、只涵蓋 data_window 內的日期，須照實說明起始日，不得把「事件資料很短」說成「事件很少發生」。
boundaries:
  - 這是描述性歷史統計：只能說「過去 n 件事件在之後 N 個交易日的實際漲跌是…」，不得說成訊號、預測、排行或進出場依據，也不得推論下一次事件會如何。
  - 每次引用數字必須同時給樣本數 n 與觀察期；status 為 insufficient_sample 的觀察期不得自行估計或補數字，要明說樣本不足。
  - 必須提到 limitations 中的主要項目，特別是樣本期間短、事件集中在少數日期 (有效樣本小於 n)、未還原股利與未計交易成本。
  - 超額報酬是相對「全市場一般股票中位數」的差，不是相對大盤指數，須說明基準。
  - 減資恢復買賣只能逐檔查詢 (需指定 stock_id)，沒有全市場歷史；不指定個股時如實說明，不得改用整市場掃描。
  - 不提供目標價、不給買賣或持有建議、不做部位大小建議。
  - 所有內容僅為資訊參考，不構成投資建議。
resources: []
outputs: markdown
---

# Event study (historical statistics only)

## Procedure

1. Pick `event_type` from the question: `disposition_start` (進入處置), `disposition_end` (出關 /
   處置結束), `attention_listed` (列入注意股), `capital_reduction_resume` (減資恢復買賣, needs a
   symbol), `buyback_start` / `buyback_end` (庫藏股買回起日 / 結束).
2. Call `get_event_study(event_type, symbol?, start_date?, end_date?)`. Leave `horizons` at the
   default (5, 20, 60 trading days) unless the user names others. Pass `symbol` only when the user
   asks about one stock; the result then also lists that stock's own past events.
3. Open the answer with the event definition and the `data_window`: how far back the event feed
   really goes and the latest price date. If the feed is short, say that this limits the sample;
   it does not mean the event is rare.
4. For each horizon report `n_events` (and `n_symbols`, `n_event_days`) before any statistic, then
   the mean and median return and the mean and median excess return versus the market median. A
   horizon with `status = insufficient_sample` gets its counts and the sentence that there are too
   few events for statistics; never fill it in.
5. Mention the pre-event move only as background, and the by-year table only when its cells have
   statistics. State plainly that events that share a day share the market's move, so the
   independent sample is smaller than n.
6. End with the main `limitations`. Keep the wording descriptive: what happened in the past cases,
   never what to do or what will happen.
7. If the tool reports `data_available = false`, relay `unavailable_reason` and stop; do not
   substitute figures from memory.
