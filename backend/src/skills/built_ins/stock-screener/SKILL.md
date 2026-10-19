---
name: stock-screener
version: 1.1.0
description: 篩選器 — 標籤篩選 (get_tag_catalog + run_tag_screener, TW 夜間批次標籤) 與數值條件篩選 (get_factor_catalog + get_screener_presets + run_screener, TW/US/ETF 閘門→條件漏斗)。Descriptive filter, never a ranked recommendation.
triggers: [篩選器, 選股, 標籤, 外資連買, 均線, 爆量, 條件篩選, 篩選條件, 預設篩選, 因子篩選, screener, tag screener, stock filter, technical tags, screener preset, factor screen]
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
tools: [get_tag_catalog, run_tag_screener, get_factor_catalog, get_screener_presets, run_screener]
output_structure:
  - '可用標籤目錄: 依類別分組 (技術面/籌碼面/熱門/智能)，中英文名稱 + 說明'
  - '篩選結果: 符合條件的個股清單，逐檔列出實際命中的標籤'
  - '資料時效: 標籤依據哪個交易日的批次資料 (as_of)'
  - '未知標籤揭露: 若請求的標籤鍵不存在於目錄，須明確說明，不得靜默忽略'
  - '數值條件篩選: 使用的市場 (TW/US/ETF)、每個條件 (因子/運算子/數值)、AND/OR 模式、已套用的閘門 (流動性/價格/市值/處置)，以及符合清單與總數'
  - '預設篩選說明: 若引用預設篩選 (preset)，列出其條件組合，並說明只是描述性的起點'
freshness_rule: run_tag_screener 讀取的是夜間批次寫入的 screener_flags Parquet 分區（前一個/最近一個批次交易日），絕非即時盤中計算；輸出必須標示 as_of 的批次日期，讓使用者知道這是「以批次資料為準」而非當下即時狀態。run_screener 讀取的是已入庫的每日／批次數值欄位，同樣不是盤中即時計算；須標示其回傳的資料日期，若某因子缺值或資料來源未就緒，照實說明。
boundaries:
  - 一律先呼叫 get_tag_catalog 確認標籤鍵存在，不得憑記憶或猜測拼出一個標籤鍵餵給 run_tag_screener。
  - 篩選結果是描述性的「目前符合條件的個股清單」，不得包裝或暗示為「值得買進的股票」或任何形式的投資建議。
  - AND 模式代表所有標籤都必須成立；OR 模式代表任一標籤成立即可 — 呈現結果時要明確說明使用了哪種模式。
  - 數值條件篩選一律先呼叫 get_factor_catalog 確認該市場 (TW/US/ETF) 真正可用的因子與欄位，不得憑記憶自創因子名稱或單位；使用者要「常見起手式」時可先呼叫 get_screener_presets，預設篩選只是描述性的起點，不是推薦組合。
  - run_screener 的閘門 (流動性/價格/市值/處置) 預設會先排除不合格標的；呈現結果時須說明有套用閘門，使用者調整閘門才可能出現被排除的標的。結果是描述性的清單與總數，不是排序後的「優選」。
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
   events like volume surges and 20-day/52-week highs and 52-week lows; 智能 pre-bundled AND-combinations of the
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

## Numeric condition screener (TW / US / ETF)

8. When the caller asks for numeric conditions (e.g. "本益比低於 15 且殖利率高於 4%") or a market other than
   TW, use the factor path: call `get_factor_catalog(market)` first to see the real filterable
   fields (and, for TW, the boolean signal tags), optionally `get_screener_presets(market)` when
   the caller wants a starting point, then `run_screener(market, conditions, match, gate_overrides,
   count_only, size)`. Tag-only questions on TW still go through `run_tag_screener`.
9. State the market, each condition, the AND/OR mode and the gates that were applied. Use
   `count_only=true` when the caller only asks "how many". Relay the tool's own detail when a
   factor is unknown or its data is not ready — never drop a condition silently and report a
   broader match set.
10. Present the result as a descriptive filter ("these symbols currently match the conditions as
    of <date>"), never as a ranked or recommended list.
