---
name: stock-screener
version: 1.0.0
description: 標籤篩選器 — discover available end-of-day tags via get_tag_catalog, then match TW symbols against them (AND/OR) via run_tag_screener. Descriptive filter, never a ranked recommendation.
triggers: [篩選器, 選股, 標籤, 外資連買, 均線, 爆量, screener, tag screener, stock filter, technical tags]
design_pattern: tool_wrapper
inputs:
  tags:
    type: array
    required: true
  match:
    type: string
    required: false
  as_of:
    type: string
    required: false
tools: [get_tag_catalog, run_tag_screener]
output_structure:
  - '可用標籤目錄: 依類別分組 (技術面/籌碼面/熱門/智能)，中英文名稱 + 說明'
  - '篩選結果: 符合條件的個股清單，逐檔列出實際命中的標籤'
  - '資料時效: 標籤依據哪個交易日的批次資料 (as_of)'
  - '未知標籤揭露: 若請求的標籤鍵不存在於目錄，須明確說明，不得靜默忽略'
freshness_rule: run_tag_screener 讀取的是夜間批次寫入的 screener_flags Parquet 分區（前一個/最近一個批次交易日），絕非即時盤中計算；輸出必須標示 as_of 的批次日期，讓使用者知道這是「以批次資料為準」而非當下即時狀態。
boundaries:
  - 一律先呼叫 get_tag_catalog 確認標籤鍵存在，不得憑記憶或猜測拼出一個標籤鍵餵給 run_tag_screener。
  - 篩選結果是描述性的「目前符合條件的個股清單」，不得包裝或暗示為「值得買進的股票」或任何形式的投資建議。
  - AND 模式代表所有標籤都必須成立；OR 模式代表任一標籤成立即可 — 呈現結果時要明確說明使用了哪種模式。
  - 標籤本身（例如「外資連3買」）是描述性事件旗標，不是訊號；一檔股票同時命中多個技術面標籤不代表更強的買賣建議。
resources: []
outputs: markdown
---

# TW tag screener (descriptive filter, non-trading)

## Procedure

1. Before constructing any query, call `get_tag_catalog()` to see the current, real set of tag
   keys, grouped by category. Never guess a tag key or invent one that "sounds right" — the
   catalog is the only source of truth for what `run_tag_screener` accepts, and it has grown
   across dispatches (currently four categories: 技術面 technical signals like moving-average
   crosses and candlestick patterns; 籌碼面 chip/institutional-flow streaks; 熱門 momentum
   events like volume surges and 52-week highs/lows; 智能 pre-bundled AND-combinations of the
   other categories' tags, e.g. a breakout confirmed by a volume surge on the same day).
2. Map the caller's request onto one or more real tag keys from the catalog. If the caller's
   phrasing doesn't clearly map onto any cataloged tag, say so rather than picking the closest
   guess and presenting it as what they asked for.
3. Choose `match: "AND"` when the caller wants stocks satisfying every named condition
   simultaneously, or `match: "OR"` when they want stocks satisfying any one of several
   conditions. State which mode was used in the response so the result isn't ambiguous.
4. Call `run_tag_screener(market, tags, match, as_of, size)`. This reads a nightly-computed flag
   partition for the requested (or latest available) trading day — it is never a live, intraday
   evaluation. Always disclose the `as_of` date the result carries as the freshness baseline.
5. Present each hit's `matched_tags` verbatim (a negated condition that held appears in its
   `not <tag>` form) — this is the "show the work" transparency the tool itself guarantees, and
   the skill must not collapse it into a bare symbol list.
6. If a requested tag key was unrecognized, the tool's `detail` field will say so — relay that
   honestly rather than silently dropping the unknown tag from the query and reporting a
   narrower match set without explanation.
7. Present the final list as a descriptive filter only: "these symbols currently match tag X (as
   of the last batch)" — never as "these are good stocks" or any ranked/recommended framing.
