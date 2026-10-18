---
name: theme-lookup
version: 1.1.0
description: 主題/產業鏈/供應鏈/關聯圖譜查詢 — what themes exist, what theme a stock is in, what stocks are in a theme (concept themes with 上游/中游/下游/相關 positions), a theme's upstream/midstream/downstream breakdown, a company's supply-chain relations (suppliers, customers, competitors with confidence and sources), where it sits in its industry chain (上游/中游/下游 with the reasons), and its entity graph (what its reports mention and which companies share it).
triggers: [主題, 概念股, 題材, 產業鏈, 供應鏈, 供應商, 客戶, 同業, 上游, 中游, 下游, 關聯圖譜, 共同提及, theme, concept, supply chain, supplier, customer, competitor, upstream, downstream, entity graph, 相關個股]
design_pattern: tool_wrapper
inputs:
  theme_key:
    type: string
    required: false
  stock_id:
    type: string
    required: false
tools: [get_canonical_context, get_themes, get_theme_members, get_symbol_themes, get_theme_tiers, get_theme_tier_heatmap, get_supply_chain_edges, get_chain_position, get_entity_graph]
output_structure:
  - '主題成員: 個股清單，每檔標明子產業、信心分數、來源（產業鏈 / AI 研究）'
  - '個股所屬主題: 反查一檔個股屬於哪些主題'
  - '產業鏈分層: 上游 / 中游 / 下游，每項標明 curated（人工審定）或 heuristic（自動分類）'
  - '概念題材: 說明、相關題材，以及成員的位置（上游 / 中游 / 下游 / 相關公司）'
  - '供應鏈關係: 供應商 / 客戶 / 同業，每條標明可信度（高 / 中 / 低）、資料日期與來源'
  - '產業鏈位置: 公司在各產業鏈中的位置（上游 / 中游 / 下游）與判定依據；證據不足時說明尚未分類'
  - '關聯圖譜: 公司提及的實體（技術 / 材料 / 應用 / 產品 / 公司）、共同提及的同業，以及已驗證的供應關係'
freshness_rule: 主題與成員為已入庫的分類資料；概念股成員附信心分數與位置（來源為整理的題材資料，非編輯保證），須明確標示，不得等同於人工審定的產業鏈事實。關聯圖譜為已入庫的提及次數統計，僅是描述性的關聯，不代表供應關係。
boundaries:
  - 不提供目標價、評等或買賣建議。
  - 概念股成員（相對於產業鏈成員）附有信心分數，非編輯保證；輸出須逐檔顯示 confidence 與 source，不得隱藏此區別。
  - 關聯圖譜的「共同提及」只表示兩家公司的資料提到相同的實體，不是供應或合作關係；只有 kind 為 supply 的連線才是已驗證的供應關係。
  - 產業鏈分層若為 heuristic（自動分類），須與 curated（人工審定）明確區分，不得將自動猜測呈現為同等權威。
  - 供應關係的可信度僅反映來源數量與品質（單一報導為低），不是保證；須逐條顯示可信度與日期，不得把低可信度的連線說成確定的事實；沒有資料時說明「尚無已知資料」，不要自行推測。
  - 結果標示 locked / concept_locked 時為預覽，完整內容屬於 Pro 方案；須說明這是預覽、不得重試或改用其他工具取得被隱藏的項目。
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
   editorial fact at confidence 1.0; a concept member carries a lower confidence score —
   never present the latter as equally guaranteed as the former.
5. **Concept themes** (`kind` = `concept`, e.g. a technology wave) are cross-industry cuts. In
   `get_theme_members` they carry a `description`, `related_keys` (other concept themes) and, per
   member, a `position` of `upstream` / `midstream` / `downstream` / `related` (the 相關公司 bucket).
   Present members grouped by position, keep the `confidence` and `source` of each, and offer
   `related_keys` as follow-ups rather than expanding them.
6. **"what does X mention / who is related to X / 關聯圖譜"** → `get_entity_graph(id, market)`. The
   focus node is the company; entity nodes (technology, material, application, product, company)
   are weighted by how often its reports mention them; peer companies are those sharing entities.
   Summarise the top entities and the peers with the most shared entities, and state plainly that a
   `cooccurrence` link means shared mentions only. Only `supply` links (supplier -> customer) are
   verified supply relations. If the graph holds just the company, say nothing is indexed for it.
7. **"who are X's suppliers / customers / competitors"** → `get_supply_chain_edges(id, market)`. Edges
   run supplier -> customer (`supplies`) or are rivalry (`competes`). Group them by role relative to
   the focus company, and show each edge's `grade` (high / medium / low), `as_of` date, `product`
   (when present) and `sources` (publisher names) so the reader sees why it is listed. An empty
   result means no relationship is known, not that none exists.
8. **"where does X sit in its industry chain / is it upstream or downstream"** →
   `get_chain_position(id, market)`. Report each chain's position with its `method` and `reasons`;
   when `confidence` is low or the position came from a sub-industry classification rather than the
   company itself, say so. No result means "not yet classified" - do not guess a position.
9. **"which themes exist / list the concept themes"** → `get_themes` (add `kind` when the user
   names one); `get_theme_tier_heatmap(theme_key)` only when the user asks how a chain's
   sub-industries are moving.
10. For tiers, never let a `heuristic` assignment read as authoritative as a `curated` one — label
   each group's assignments so the reader can tell them apart.
11. **Previews (Pro plan).** The full concept-theme member list, supply relations, chain positions and
   the entity graph are part of the Pro plan. For other users these tools return a preview: the result
   carries `locked: true` (or `concept_locked: true` on a theme list / a symbol's themes), a
   `hidden_count` and an `upgrade_note`. Present exactly what came back, say plainly that this is a
   preview and that the full view is part of the Pro plan (use the `upgrade_note`), and give the
   `hidden_count` when it helps. **Never retry, rephrase or call another tool to get the withheld
   items** - the limit is applied by the server, so a retry returns the same preview and only spends
   a step. Industry themes and their tiers are never limited.
