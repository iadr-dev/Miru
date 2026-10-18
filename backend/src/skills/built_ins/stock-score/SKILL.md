---
name: stock-score
version: 1.1.0
description: 多因子綜合評分 — one stock/ETF's cached 5-factor composite score + descriptive band, or the whole-TW-universe leaderboard of strongest/weakest cached scores.
triggers: [評分, 綜合評分, 多因子, 排行榜, 強勢股, 弱勢股, AI 摘要, SWOT, 優劣勢, stock score, factor score, leaderboard, momentum score, ai summary]
design_pattern: tool_wrapper
inputs:
  stock_id:
    type: string
    required: false
  profile:
    type: string
    required: false
  direction:
    type: string
    required: false
  limit:
    type: integer
    required: false
tools: [get_canonical_context, get_stock_score, get_score_leaderboard, get_stock_summary]
output_structure:
  - '單股評分: 綜合分數 (0-100，可能為 null) + 描述性區間 (偏多/中性/偏空)'
  - '五因子拆解: momentum / trend_quality / value_guard / quality / revenue_flow，逐項百分位或缺值原因'
  - '缺值揭露: partial_factors 中列出的因子須明確說明「無法取得」，不得以中性值填補'
  - 'AI 摘要 (使用者要摘要/SWOT 時): 標題、敘述與 SWOT，內容僅來自該檔已算出的評分，須註明為評分的文字整理 (Sponsor)'
  - '排行榜: 依 direction (bullish/bearish) 排序的 Top-N 名單，標明批次計算日期 (as_of)'
freshness_rule: 評分與排行榜皆為快取讀取，AI 摘要則只根據已算出的評分改寫，讀取當日（或最近一次）夜間批次寫入的結果，絕不即時計算；若當日批次尚未產出，get_stock_score 回傳 found=false 並附原因，get_score_leaderboard 回傳空清單並附原因。輸出必須標明 as_of 批次日期。
boundaries:
  - 評分與五因子拆解為描述性因子脈絡，絕非買賣/持有訊號或目標價；偏多/中性/偏空僅描述目前因子組合的樣貌，不是操作建議。
  - 任一因子 percentile 為 null 時，須誠實揭露該因子目前無法取得（partial_factors），不得捏造中性值替代。
  - swing（預設，短波段權重）與 position（中長期權重）是兩種不同的評分視角，須先確認或依問題語境判斷使用哪一種，不得永遠預設 swing 而不說明。
  - get_score_leaderboard 是整個 TW 市場的每日批次排名快取讀取，never 即時全市場掃描；一律標示其為「截至上次批次」的結果，不得暗示為即時排名。
  - 排行榜本身是描述性排序，不得包裝成「應買入」清單。
  - get_stock_summary 的標題、敘述與 SWOT 嚴格取材自該檔已算出的評分，不得加入評分以外的事實或預測；找不到評分 (found=false) 時照實說明，不得自行補寫摘要。它是 Sponsor 資料，未取得授權時針對遭遮蔽區塊誠實說明。SWOT 是描述性整理，不是買賣、持有或目標價建議。
resources: []
outputs: markdown
---

# Multi-factor stock score (descriptive, non-trading)

## Procedure

1. If the question concerns one symbol, call `get_canonical_context(stock_id, market)` first for
   a currentness baseline.
2. Decide which horizon profile fits the question before calling `get_stock_score`:
   - **swing** (default) — shorter-horizon factor weighting, for questions about near-term
     momentum/trend.
   - **position** — longer-horizon factor weighting, for questions about a multi-month/
     multi-quarter view.
   If the caller's intent is ambiguous, ask which horizon they mean rather than silently
   defaulting to `swing` and presenting it as the only lens.
3. Call `get_stock_score(id, market, profile)`. If `found` is `false`, say plainly that today's
   scoring batch has not yet produced a row for this symbol — never substitute a stale cached
   value or a live estimate.
4. When `found` is `true`, present the five real factor names verbatim — `momentum`,
   `trend_quality`, `value_guard`, `quality`, `revenue_flow` — each with its percentile (or, if
   `None`, its `unavailable_reason`). Any factor named in `partial_factors` must be called out
   explicitly as genuinely unavailable for this symbol/date, never silently smoothed over or
   defaulted to a neutral 50th percentile.
5. Present the `band` (偏多/中性/偏空) as descriptive context about the current factor mix only —
   never rephrase it as an instruction to buy, sell, hold, or adjust a position, matching the
   non-trading discipline every other descriptive-score surface in this registry follows.
6. For a "which stocks are strongest/weakest right now" question, call
   `get_score_leaderboard(market, profile, direction, limit)`. Always state the `as_of` date the
   response carries, and describe the list as "as of the last nightly batch run" — never as a
   live, continuously-updating ranking. `direction=bullish` returns the strongest composites
   first, `bearish` the weakest — present both as a sorted read of the same daily cache, not a
   trade signal or recommendation list.
7. If the leaderboard comes back empty, relay the tool's own message (the batch hasn't run yet
   for that market/profile) rather than implying no strong/weak stocks currently exist.
8. When the caller asks for a plain-language summary or SWOT of one stock/ETF, call
   `get_stock_summary(id, market)` after the score. It rewrites the already-computed composite
   into a headline, narrative and SWOT; if `found` is `false` there is no score yet, so say that
   rather than writing a summary from memory. Present it as a descriptive text version of the
   score, never as an instruction to buy, sell or hold.
