---
name: theme-lookup
version: 1.0.0
description: 主題/產業鏈查詢 — what theme a stock is in, what stocks are in a theme, and a theme's upstream/midstream/downstream breakdown (industry-chain facts vs AI-researched concept members clearly distinguished).
triggers: [主題, 概念股, 產業鏈, 上游, 中游, 下游, theme, concept, supply chain, upstream, downstream, 相關個股]
design_pattern: tool_wrapper
inputs:
  theme_key:
    type: string
    required: false
  stock_id:
    type: string
    required: false
tools: [get_canonical_context, get_theme_members, get_symbol_themes, get_theme_tiers]
output_structure:
  - '主題成員: 個股清單，每檔標明子產業、信心分數、來源（產業鏈 / AI 研究）'
  - '個股所屬主題: 反查一檔個股屬於哪些主題'
  - '產業鏈分層: 上游 / 中游 / 下游，每項標明 curated（人工審定）或 heuristic（自動分類）'
freshness_rule: 主題與成員為已入庫的分類資料；概念股成員為 AI 研究、附信心分數，須明確標示，不得等同於人工審定的產業鏈事實。
boundaries:
  - 不提供目標價、評等或買賣建議。
  - 概念股成員（相對於產業鏈成員）附有信心分數且為 AI 研究，非編輯保證；輸出須逐檔顯示 confidence 與 source，不得隱藏此區別。
  - 產業鏈分層若為 heuristic（自動分類），須與 curated（人工審定）明確區分，不得將自動猜測呈現為同等權威。
resources: []
outputs: markdown
---

# Theme / supply-chain lookup

## Procedure

1. Call `get_canonical_context(stock_id, market)` for the currentness baseline whenever the
   question is about a specific stock; skip it for a pure "what's in theme Y" browse.
2. Route by the question:
   - **"what theme is X in"** → `get_symbol_themes(id, market)`. List every theme the symbol
     belongs to, showing each membership's sub-industry and confidence.
   - **"what's in theme Y"** → `get_theme_members(theme_key)`. List the member symbols, and for
     each show its `sub_industry`, `confidence`, and `source`. If `found` is false, say the theme
     key isn't known rather than inventing members.
   - **"what's upstream vs downstream in theme Y"** → `get_theme_tiers(theme_key)`. Present the
     upstream / midstream / downstream groups, and for each sub-industry show whether its tier is
     `curated` (editorially verified) or `heuristic` (automatically classified).
   - **combined "which themes is X in, AND what's upstream/downstream in each"** — `get_symbol_themes`
     can return several themes for one stock, and expanding every one of them into its own
     `get_theme_tiers`/`get_theme_members` call burns one tool-call pair per theme against this
     turn's fixed step budget; a stock in 4+ themes can exhaust the budget before synthesis ever
     runs, silently dropping every result already gathered. Cap tier/member expansion to **at
     most the 2 themes most relevant to the question's own wording** (prefer an explicitly named
     industry, e.g. "semiconductor", over a tangential membership) — list every theme name from
     `get_symbol_themes` first, then note in the answer that tier detail is limited to those 2 and
     name the rest so the user can ask a follow-up for any theme not expanded.
3. **Never guess a second theme key.** Call `get_theme_tiers`/`get_theme_members` with your best
   single reading of the caller's own wording (e.g. "半導體" for a semiconductor question) exactly
   ONCE per theme. If that call returns `found: false`, treat the theme database as not covering
   this theme at all — do NOT retry with a different spelling/synonym/English translation of the
   same key ("IC設計", "semiconductor", "晶片", ...). Each retry is a real tool-call step against
   this turn's fixed budget, and burning several of them hunting for a working key spelling is
   the single most common way this skill exhausts its budget before ever reaching synthesis,
   discarding every result already gathered. On the first `found: false`, go straight to a labeled
   web-search supplement instead (state plainly that the structured theme database has no entry
   for this key, then cite third-party sources for the rest) — this is honest and correct
   behavior, not a fallback of last resort.
4. Always surface the `source`/`confidence` distinction verbatim. An industry-chain member is an
   editorial fact at confidence 1.0; a concept member is AI-researched with a confidence score —
   never present the latter as equally guaranteed as the former.
5. For tiers, never let a `heuristic` assignment read as authoritative as a `curated` one — label
   each group's assignments so the reader can tell them apart.
