# API Reference

Consolidated reference for all interfaces: **REST**, **SSE**, and **MCP**. Endpoints are
served by `backend/src/api/` (FastAPI) and `backend/src/mcp/` (FastMCP). Per-module detail
is linked inline. All routes are **read-only w.r.t. market data**; the only writes are
product/app state (threads, memory, schedules…).

**Conventions**
- Base path `/api/v1`. Auth via session cookie or `Authorization: Bearer`.
- Anonymous allowed for public market/context reads; AI chat requires sign-in.
- Errors: JSON `{code, message, retryable}`. Unentitled AI features return a **teaser
  envelope** `{locked, required_tier, preview}` (HTTP 200), never 401 — see
  [modules/12](modules/12-multi-user-tiers.md).
- `Idempotency-Key` honored on turn-creating POSTs.
- **Read-model freshness** (cache coherence — [modules/16](modules/16-infrastructure.md) §3a):
  every cacheable read (`/quote`, `/analytics/*`,
  `/market/*`) returns an **`as_of`** timestamp (value production time) + a strong **`ETag`**;
  `If-None-Match` → **`304`**. `Cache-Control: private, max-age=0, stale-while-revalidate=<class
  boundary>`. A **`data.updated`** SSE event (`{capability, market, as_of, scope}`) fires when a
  derived-compute batch lands so clients invalidate precisely instead of waiting a TTL. The `quote`
  SSE payload carries per-tick `as_of` + `market_open`.

---

## 1. Agent & streaming ([modules/07](modules/07-conversation-streaming.md))

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/agent/query` | Start a turn (thread_id optional → auto-creates); returns SSE. **Real request body today is `{message, thread_id?, web_search?, deep_research?, locale?, attachments?}`** (`api/routers/agent.py`'s `AgentQueryRequest`) + the `Idempotency-Key` header — the classifier alone decides framework/skills routing server-side. `web_search` toggles web search for the turn and `attachments` are processed via `process_attachments()`, both wired through to `start_turn_stream(web_search_enabled=body.web_search, ...)`. A per-turn `model` override is real too, but lives on the separate `PATCH /threads/{id}` route (`model_override`, below), not on this body. `deep_research:true` runs Pro-only cited web research instead of a normal reply ([24](modules/24-deep-research.md)): `403 feature_not_entitled` for non-Pro, `429 deep_research_limit_reached` after 5 reports/UTC day (`regenerate`/`edit` bodies accept `deep_research?` too; omitted = re-run the way the original request ran, stored as `turns.mode`). **Not yet built:** `plan` mode, `skill_hint`. Personality resolves from `threads.personality_override` else `users.personality_id` ([modules/23](modules/23-agent-personality.md), real, wired into every turn via `PersonalityContext`) — set via `PATCH /me {personality_id}` (user default); there is currently no route that writes `threads.personality_override` itself, so a per-thread pin isn't settable yet even though the resolution seam reads it |
| GET | `/threads/{id}/stream` | Attach/resume live stream (`Last-Event-ID`) |
| POST | `/threads/{id}/steer` | Queue a **steering** message into the running turn (redirect, no cancel) |
| POST | `/threads/{id}/switch-branch` | Switch active path to another message (generates a Branch Summary) |
| POST | `/threads/{id}/turns/{tid}/cancel` | Cancel a running turn (stop SSE) |

All tools are read-only and run without approval. If a turn calls a `sponsor` (paid FinMind)
tool for an unentitled user, that section is masked at the tool-result level (`{locked,
required_tier, preview}`) and the answer proceeds around it — no confirmation step. See
governance §4.

### SSE event types (every frame carries a correlation envelope: `seq, ts, turn_id, agent_id, parent_agent_id, node_id`)
This is the full designed vocabulary; **not all of it is emitted yet** — see the
implementation-status note below.

`turn.start` · `route.decided` · `agent.spawned` · `agent.progress` · `agent.done` ·
`steering.accepted` · `activity` (NL narration) · `message.delta` · `thinking.delta` (raw,
collapsed) · `tool.proposed` · `tool.started` · `tool.result` · `tool.locked` · `tool.blocked` ·
`caveat` · `synthesis.start` · `rich_card` · `ask_user` · `follow_up` · `usage` · `turn.end` ·
`error`.
The `agent_id`/`parent_agent_id` envelope lets the client render a **live timeline with
parallel sub-agent lanes** (Claude Code-style); **`activity`** carries human-readable
narration (the primary line), while `thinking.delta`/tool internals are opt-in detail.
Full semantics + run-tree: [modules/07](modules/07-conversation-streaming.md).

> **Implementation status.** Real today: `turn.start`, `route.decided`, `agent.spawned`,
> `agent.progress`, `agent.done`, `steering.accepted` (now genuinely mid-flight-redirecting, not
> just recorded), `caveat`, `synthesis.start`, `usage`, `turn.end`, `tool.result`, and `rich_card`.
> **Not yet built:** `activity`, `message.delta`, `thinking.delta`, `tool.proposed`,
> `tool.started`, `tool.locked`, `tool.blocked`, `ask_user`, `follow_up`,
> `pet.reaction`/`pet.leveled`/`pet.pull_earned` — the agent loop returns one whole `TurnResult`
> per turn, not a token/tool-level stream, so a masked `sponsor` tool result comes back inline in
> the final answer/`tool.result` payload rather than as a dedicated `tool.locked` frame. See
> [modules/07](modules/07-conversation-streaming.md)'s own implementation-status note for detail.

---

## 2. Threads / history ([modules/13](modules/13-chat-interaction-features.md))

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/threads` | Create thread |
| GET | `/threads?include_archived=` | List mine. **Real query surface today is just `include_archived: bool` (default `false`)** (`api/routers/threads.py`'s `list_threads`) — `sort`/`filter`/`q`/pagination are not yet implemented as query params; use `GET /threads/search?q=` (below) for a text query |
| GET | `/threads/{id}` | Thread + history |
| PATCH | `/threads/{id}` | Rename title, star/pin, archive, tag, set per-thread `model_override` |
| DELETE | `/threads/{id}` | Soft-delete → purge |
| POST | `/threads/{id}/fork` | Fork from a turn |
| GET | `/threads/search?q=` | Full-text search (title + messages) |
| GET | `/threads/{id}/export?format=md\|json` | Export |

### Message actions
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/threads/{id}/turns/{tid}/feedback` | 👍/👎 + optional comment — a real, persisted, upsert-on-resubmit rating (`(turn_id, user_id)` unique). No `model` override yet. |
| POST | `/threads/{id}/turns/{tid}/regenerate` | Re-run the same user input, creating a NEW sibling assistant turn (SSE, same shape as `/agent/query`) — the original is untouched. No `model` override param yet. |
| POST | `/threads/{id}/turns/{tid}/edit` | Edit a user message + resend: creates a new sibling user turn + child assistant turn (SSE) — the original user turn is untouched. Branch is real and `switch_branch`-navigable; automatic BranchSummaryEntry generation on the branch left behind is not yet wired (no cheap-role `LLMClient` handle at this seam today). |

---

## 3. Voice ([modules/13](modules/13-chat-interaction-features.md))

| Method | Path | Purpose | Tier |
|--------|------|---------|------|
| POST | `/voice/transcribe` | STT: `{audio_base64, language?}` (base64 m4a/webm-opus, hard-capped ~12MB) → **transcript + confidence**; `language` default `zh-TW`; the caller's own watchlist symbols are fed in as vocabulary-biasing hints (no separate `hints` request field). **Returns text only — does NOT create a turn** (user confirms/edits, then `POST /agent/query`) | **Free** (quota-bounded; the beginner ask-loop) |
| POST | `/voice/speak` | TTS: `{text, language?}` → audio (spoken text **includes the caveat**, guaranteed by `voice.service.speak`). **No `turn_id` input today** — the caller always supplies the literal text to speak, never a turn reference | Premium+ (`tts_read_aloud` feature flag) |

Raw audio is discarded after transcription (PII/PDPA); low-confidence/ambiguous symbols →
`ask_user`. STT/TTS are separate config-selected providers (self-host whisper or cloud).

---

## 4. Context & market data (read-only)

| Method | Path | Purpose | Access |
|--------|------|---------|--------|
| GET | `/context/{package}` | Canonical or conversation package (`canonical`, `conversation`) | public |
| GET | `/search?q=&market=US` | **Symbol type-ahead — real coverage is narrower than the design.** `api/routers/context.py`'s `symbol_search` dispatches the one built search tool (`get_symbol_search`, yfinance-backed) for **US/GB/JP/EU only**; `market=TW` (and ETF) returns HTTP 501 `not_supported` — there is no ingested `TaiwanStockInfo`/ETF-list/registered-US-symbol universe search service backing a TW/ETF type-ahead today, so the client-cacheable grouped-TW/ETF/US-with-主題/產業-matches shape below is design, not built | public |
| GET | `/quote?symbols=&market=` | EOD / last-snapshot price (`price_eod`) — one-shot | public |
| GET | `/quote/stream?symbols=&market=` | **Live-ish quote feed (SSE)** — `quote` events (each with `as_of` + `market_open`) pushed ~5s while market-open via the shared demand-driven poller (governance §9b, [modules/16](modules/16-infrastructure.md) §2a); delayed snapshot, not true streaming | **Premium+ (masked teaser / EOD otherwise)** |
| GET | `/updates/stream` | **Cache-invalidation feed (SSE)** — `data.updated` events `{capability, market, as_of, scope}` when a derived-compute batch lands ([modules/16](modules/16-infrastructure.md) §3a, [modules/22](modules/22-data-ingest.md) §2); lets clients revalidate precisely | public |
| GET | `/sources` | Source adapter matrix / capabilities | public |
| GET | `/health` | Source-health view (per-capability currentness/status; no live FinMind quota reporting — the pool is a static config ceiling, not a polled quota, see [governance.md](governance.md) §9) | public |

Data inventory: [data-catalog.md](data-catalog.md). Responses containing `sponsor` (paid
FinMind) fields are masked (`{locked, required_tier, preview}`) for unentitled users — the
frontend blurs them with an upgrade CTA. No approval endpoint exists.

**Ops probes** (`api/routers/probes.py`, mounted at the app root — not under `/api/v1`, no
auth): `GET /livez` (process up, never touches a datastore), `GET /readyz` / `GET /startupz`
(Postgres always + Redis when `event_bus_backend`/`rate_limiter_backend`=redis; 503 with a
per-dependency breakdown on failure). Distinct from the business-facing `/health` above (data
currentness + FinMind quota, not infra state). `GET /healthz` is a third, even simpler liveness
route registered directly on the app (not a router) for the MCP-style "is anything listening"
check. See [modules/17](modules/17-devops-operations.md).

## 4a. Analytics, scoring & screener ([modules/20](modules/20-analytics-scoring.md))

| Method | Path | Purpose | Access |
|--------|------|---------|--------|
| GET | `/analytics/{symbol}/indicators?set=ma,kd,macd,rsi,bollinger,volume,sr,wave` | Indicator + 量能/支撐壓力/N波 values + series | public |
| GET | `/analytics/{symbol}/score?market=TW&profile=swing\|position` | **real** — cached lean 5-factor 多空 composite + 偏多/中性/偏空 + breakdown + regime/non-trading/normalization caveats; `found=false` if today's batch hasn't scored this symbol yet (cache-only, never live-computed) | public |
| GET | `/analytics/{symbol}/summary` | AI 智能摘要 + 市場定位 + SWOT | Premium+ (teaser otherwise) |
| GET | `/analytics/screener/factors?market=TW\|US\|ETF` | Bilingual catalog + **per-market `fields[]`** (unit/min/max/step/enum/preset-chips) + presets | public |
| POST | `/analytics/screener/run` | **Funnel** — gates→setup: `{market, preset\|screen_type\|conditions[], match, trade_date?, gate_overrides?, count_only?}` (custom AND/OR builder; `count_only` = survivor probe) | public/sponsor per criteria |
| GET | `/analytics/rankings?market=TW&profile=swing&direction=bullish\|bearish&limit=20&as_of=` | **real** — top/bottom-N (max 50) whole-TW-universe composite-score leaderboard, a sort-and-slice read of the daily batch's cache, never a live rescan | public |
| GET | `/analytics/themes` | LLM-discovered themes + 資金流向 (tag, not scored) | Premium+ |
| GET | `/analytics/screener/screens` | list this user's saved screens (per market) | user-scoped |
| POST | `/analytics/screener/screens` | create `{name, market, conditions, match, gate_overrides}` | user-scoped |
| PATCH | `/analytics/screener/screens/{id}` | rename / update conditions | user-scoped |
| DELETE | `/analytics/screener/screens/{id}` | remove | user-scoped |

> **Per-market screening.** `market=TW` (FinMind columnar: 技術/基本面/動能/籌碼) · `market=ETF` =
> `(TW,etf)` (折溢價/AUM/受益人/持股) · `market=US` = yfinance query (估值/成長/動能/分析師, **no 籌碼**),
> **phased** (module 19) — returns `not_supported` until its adapters land, which the client degrades to
> "尚未支援". Factor availability is gated by adapter `supports()` (module 19 §3), not the UI.

All are **non-trading, caveated, factor-transparent** (module 20 §7): descriptive bands, no
buy/sell/target/sizing; scores ship their breakdown; analyst externals attributed.

> **Implementation status.** `/analytics/{symbol}/score` and `/analytics/rankings` (marked
> **real** above) are backed by real code: `api/routers/analytics.py`, dispatched through
> `tools/score_tools.py`'s `get_stock_score`/`get_score_leaderboard` onto the relational
> `stock_scores` cache the daily `analytics/scoring_batch.py` batch writes. None of the other
> `/analytics/*` endpoints above are backed by real code yet — no `scoring/`/`screener/`/
> `summary/`/`themes/` subpackages, no `run_screener`/`get_top_stocks`/`get_ai_summary`/
> `get_swot`/`get_themes` tool ([module 20](modules/20-analytics-scoring.md) implementation-status
> note). Separately, a **real, already-built** `run_equity_screener_us` agent tool
> (`tools/analyst_tools.py`, over `sources/yfinance/screener.py`) covers US/GB/JP/EU equity/ETF/
> fund screening today — it is a distinct tool from the `market=US` row of the not-yet-built
> `/analytics/screener/run` above (deliberately not named `run_screener` to avoid colliding with
> that future name) and has no REST projection listed in this doc yet.

## 4a-2. Market, derivatives & macro (REST over the module-03 tools)

Thin REST (or RSC/BFF) surface over the market/derivatives/macro tools — the dashboard + new
screens need these as HTTP, not just agent tools.

| Method | Path | Purpose | Access |
|--------|------|---------|--------|
| GET | `/market/overview` | index + breadth + regime (`get_market_context`) | public |
| GET | `/market/advance-decline` | 漲跌家數 + >5%/3–5% buckets (`get_advance_decline`) | public |
| GET | `/market/index-contribution?index=TAIEX` | per-stock 點貢獻 (`get_index_contribution`) | public |
| GET | `/market/sectors` | 產業別 sub-indices + rotation (`get_sector_indices`) | public |
| GET | `/market/institutional-ranking` | top net-buy/sell stocks (whole-market 三大法人) | public |
| GET | `/market/margin-maintenance` | 大盤融資維持率 risk (`get_margin_maintenance`) | public |
| GET | `/market/day-trading` | 當沖熱門/比重排行 (`get_day_trading`) | public |
| GET | `/market/etf-money-flow` | cross-ETF 資金流向 (`get_etf_money_flow`) | Premium+ (mosaic) |
| GET | `/market/industry-flow` | 產業鏈資金流 (`get_industry_money_flow`) | Premium+ (mosaic) |
| GET | `/derivatives/{contract}` | 期/選 OI · P/C · VIX · 三大法人 · 大額交易人 (`get_futures_options_daily`/`get_put_call_ratio`/`get_taiex_vix`/`get_derivatives_institutional`) | public |
| GET | `/macro` | 匯率/利率/債殖利率/黃金/原油 + Fear-Greed (`get_macro_indicators`) | public |
| GET | `/macro/business-indicator` | 景氣對策信號 燈號 (`get_business_indicator`) | public |

> **Implementation status.** 8 of the 12 rows above are real, wired routes
> (`api/routers/market_rest.py`): `/market/institutional-ranking`, `/market/margin-maintenance`,
> `/market/day-trading`, `/market/etf-money-flow`, `/market/industry-flow`,
> `/derivatives/{contract}`, `/macro`, `/macro/business-indicator`. **`/market/overview`,
> `/market/advance-decline`, `/market/index-contribution`, and `/market/sectors` are not
> built** — their named tools (`get_market_context`/`get_advance_decline`/
> `get_index_contribution`/`get_sector_indices`) were verified not to exist in the registry, so
> the routes were skipped rather than faked (see [DEVELOPMENT.md](DEVELOPMENT.md) Phase 11).

All non-trading, caveated; derivatives framed as descriptive positioning (not a directional call).

## 4a-3. Themes & supply-chain tiers (REST over the theme-lookup tools)

Thin REST over the three real theme-lookup agent tools (`tools/theme_tools.py`), which read the
already-ingested relational theme store. This is platform reference data — classification and
relationships, no scoring/directional framing — so every route is **public** (an anonymous caller
gets real data), dispatched through the same entitlement gate as the market routes. These are the
real, wired endpoints; the `Premium+` `/analytics/themes` row above (LLM-discovered themes +
資金流向) remains a not-yet-built, separate concern.

| Method | Path | Purpose | Access |
|--------|------|---------|--------|
| GET | `/themes/{key}` | one theme's identity + member symbols, each with sub-industry/confidence/source (`get_theme_members`) | public |
| GET | `/themes/{key}/tiers` | the theme's sub-industries grouped upstream/midstream/downstream, each marked curated vs heuristic (`get_theme_tiers`) | public |
| GET | `/symbols/{symbol}/themes?market=TW` | reverse lookup: every theme a symbol belongs to (`get_symbol_themes`) | public |

An unknown theme key returns `found=false` at HTTP 200, not a 404 — the caller distinguishes
"no such theme" from an error. Concept-theme membership is AI-researched and carries a confidence
score; industry-chain membership is editorially sourced — every member row surfaces both `source`
and `confidence` so the two are never conflated.

## 4a-4. Fund/analyst data & ETF overlap (REST over `analyst_tools.py`/`etf_overlap_tools.py`)

Thin REST over six previously tool-only reads that had no HTTP projection: the four
yfinance-native, non-TW-primary analyst tools (`tools/analyst_tools.py`) and the ETF-overlap /
fund-comparison pair (`tools/etf_overlap_tools.py`), dispatched through the same
`dispatch_tool` entitlement gate as every other route in this reference. Every route here is
**public** — every wrapped tool is `DataClass.PUBLIC`.

| Method | Path | Purpose | Access |
|--------|------|---------|--------|
| GET | `/funds/{symbol}/options-chain?market=US&expiry=` | option chain (calls + puts, one expiry) for one non-TW symbol (`get_options_chain`) | public |
| GET | `/funds/{symbol}/institutional-holders?market=US` | institutional + mutual-fund holder rows for one non-TW symbol (`get_institutional_holders`) | public |
| GET | `/funds/{symbol}/insider-activity?market=US` | insider (Form-4-derived) transaction rows for one non-TW symbol (`get_insider_activity`) | public |
| GET | `/funds/{symbol}/holdings?market=US` | top-holdings + sector-weighting rows for one non-TW ETF/mutual fund (`get_fund_holdings`) | public |
| GET | `/funds/overlap?symbols=SPY,VOO,IVV&market=US` | constituent-overlap comparison across **3-10** non-TW ETFs/funds — the fix for "existing tools only compare 2 funds at a time" (`get_etf_overlap`) | public |
| GET | `/funds/comparison?symbols=0050:TW,SPY:US&lookback_days=365` | total-return-with-dividends-reinvested comparison across **2-10** symbols, any market incl. TW (`get_fund_comparison`) | public |

`get_options_chain`/`get_institutional_holders`/`get_insider_activity`/`get_fund_holdings` are
restricted to `US`/`GB`/`JP`/`EU` (yfinance's non-TW-primary capability set) — a TW symbol is
rejected at HTTP 422, not silently degraded.

`/funds/overlap` and `/funds/comparison` take a comma-separated symbol list rather than a
repeated query param (`?symbols=a&symbols=b`), matching this codebase's one existing
multi-symbol convention (`/quote?symbols=2330,2317`). `/funds/comparison`'s list is
`id:market` pairs (e.g. `0050:TW,SPY:US`), since — unlike the single-market overlap-checker —
each symbol can be in a different market. An out-of-bounds symbol count (fewer than 3 for
overlap, fewer than 2 for comparison, or more than 10 for either) is rejected at HTTP 422 with
the standard `{code: "invalid_request", ...}` envelope, never a 500.

**`get_etf_overlap` was never previously mentioned anywhere in this reference — closing that
gap.** It requires 3+ non-TW symbols (never TW: per-constituent holdings/weights are a
documented, unfilled adapter gap for TW ETFs — see `sources/etf_pcf.py`'s own module
docstring), and honestly names any requested symbol whose holdings data came back unusable in
`unavailable_symbols` rather than silently dropping it. Its overlap percentage is the sum of
`min(weight_in_a, weight_in_b)` over every holding two funds both hold — the standard "portfolio
overlap" convention.

`get_fund_comparison` covers 2+ symbols in **any** market (including TW) for total return with
dividends reinvested at each ex-dividend date's closing price (a standard approximation, not an
account-level simulation of actual reinvestment timing, fees, or taxes) — but it **never**
computes overlap%/NAV/premium-discount for TW symbols, the same documented TW-holdings gap
`get_etf_overlap` discloses; a TW row's `unavailable_note` says so explicitly rather than
implying TW ETFs get the same treatment as non-TW ones.

## 4b. Pets & gamification ([modules/21](modules/21-pet-gamification.md)) — cosmetic, no tier gate

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/pets/catalog` | Full pet catalog (rarity, names, descriptions) |
| GET | `/pets` | My owned pets |
| GET | `/pets/active` | My equipped pet |
| PUT | `/pets/{record_id}/equip` | Equip (atomic unequip-others) |
| GET | `/pets/progress` | Streak, chat_count, pulls, pity, milestones, completion |
| POST | `/pets/pull` | Gacha pull (reserve-before-act on `available_pulls`) |

Companion reactions stream over the chat SSE as additive `pet.reaction` / `pet.leveled` /
`pet.pull_earned` events (rendered in a separate UI region; ignorable with no effect on answers).

---

## 5. Memory ([modules/09](modules/09-memory.md))

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/memory?scope=user` | List memories |
| POST | `/memory` | Upsert (preference/finding/entity_note — **watchlist now via §5a**) |
| DELETE | `/memory/{id}` | Delete (honored immediately) |

---

## 5a. Watchlists ([modules/09](modules/09-memory.md), [adr/0027](adr/0027-first-class-watchlist.md)) — public user data

Rich rail: named groups, TW/US/ETF tabs, drag-reorder, star toggle. All public (non-trading user
data); only the live-quote column inherits the Premium+ gate via `/quote/stream`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/watchlists?market=TW` | My groups for a market tab (ordered); omit `market` → all. **Not built: no default-preset seeding on register** — `memory/watchlists.py`'s `list_groups` is a plain per-user query with no seed/bootstrap step, so a brand-new user's rail is genuinely cold-empty until they create a group; there is no `config.default_watchlist_presets` anywhere in this codebase. Each group's item list is returned inline (no separate `item_count` field on the group view today) |
| POST | `/watchlists` | Create group `{name, market, asset_class?}` (`asset_class` defaults to `stock`; appended, lexorank end key) |
| PATCH | `/watchlists/{id}` | Rename `{name}` and/or reorder `{after_id\|before_id}`; `If-Match: <version>` |
| DELETE | `/watchlists/{id}` | Soft-delete group + cascade items |
| POST | `/watchlists/reorder` | Bulk reorder `{ordered_ids[]}` — full-array fallback for a big reshuffle |
| GET | `/watchlists/{id}/items` | Items in a group (usually returned inline by the group GET) |
| PUT | `/watchlists/{id}/items/{symbol}` | **Star toggle — add** (idempotent — a repeat add returns the existing row, never a duplicate); body `{note?}`. No `after_id` on this route — a newly-added item lands at the end of the group's sort order; use the item `PATCH` below to reorder afterward |
| DELETE | `/watchlists/{id}/items/{symbol}` | **Star toggle — remove** (idempotent; 204 regardless) |
| PATCH | `/watchlists/{id}/items/{item_id}` | Set/clear `{note, clear_note?}` — note-only; **no reorder-within-group on this route today** (`PatchItemRequest` carries no `after_id`/`before_id`) |
| POST | `/watchlists/{id}/items/{item_id}/move` | Move item to another group `{target_watchlist_id, after_id?}` |

**Batch quotes = reuse §4** — the rail fans its symbols into `GET /quote?symbols=…&market=TW` (EOD,
free) and `GET /quote/stream?symbols=…` (live-ish, Premium+). No new quote surface. Sparkline/candle
series via `GET /analytics/{symbol}/indicators` (§4a).

---

## 6. Accounts, tiers & usage ([modules/12](modules/12-multi-user-tiers.md))

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/me` | Profile + tier + entitlements |
| PATCH | `/me` | Update profile (display_name, birthday, phone+country code, nationality, **personality_id** — agent-voice preset, [modules/23](modules/23-agent-personality.md)) |
| GET | `/personality/presets` | List agent-voice presets (id, name, description, tone) for the Settings picker |
| POST | `/auth/oauth/{provider}` | Begin OAuth (google/line) — stashes `state`+PKCE `code_verifier`(+LINE `nonce`) in httpOnly cookies and returns `{authorization_url, retry_authorization_url?}` JSON for the client to navigate to (the route itself does not issue an HTTP redirect) |
| GET | `/auth/oauth/{provider}/callback` | OAuth redirect target (`?code&state`) — verify state, exchange code (secret + `code_verifier`), upsert user+oauth_account, issue JWT. LINE auto-login failure → retry w/ `disable_auto_login=true` |
| POST | `/auth/refresh` | Rotate JWT access token via refresh token |
| POST | `/auth/logout` | Revoke session |
| GET | `/me/connections` | Linked providers (google/line) — Settings |
| POST | `/auth/oauth/{provider}/link` | Link a provider to the **current** user (multi-provider = one account) |
| DELETE | `/me/connections/{provider}` | Unlink a provider (refused if last login method) |
| GET | `/usage/quota` | Resolved limits given tier + BYOK (chats/day, req/min, FinMind class, features) — the entitlement envelope, not live consumption counters |
| GET | `/usage/today` | What the caller has claimed of today's (UTC) allowances: `chats_used`, `deep_research_used`, `deep_research_limit` |
| GET | `/usage/llm?days=30` | The caller's own LLM consumption (1-90 days): a zero-filled daily series, per-(provider, model) totals, grand total. Tokens come from each provider's per-response usage metadata; `cost_micros` is non-zero only where the provider reports a charge (OpenRouter); `estimated_cost_micros` is a list-price estimate (`llm/pricing.py`) for the rest, kept separate and never added into `cost_micros`; a model with `priced: false` has neither Not the provider's bill - their usage APIs need admin credentials an inference key lacks |
| GET | `/usage?thread_id=` / `/usage/summary` | **Not built** — no route exists yet; `events/usage.py` is an in-process accumulator with no durable rollup store to serve this from |
| GET | `/me/keys` | Providers the caller has saved an LLM key for (names only, never keys) |
| POST | `/me/keys` | Store BYOK key (encrypted); `422 unsupported_provider` for an unknown provider. With a key saved, turns run on the user's key and model - `409 byok_key_required` if the picked model's provider has no key ([12 §5](modules/12-multi-user-tiers.md)) |
| DELETE | `/me/keys/{provider}` | Remove BYOK key |

---

## 7. Scheduler & alerts ([modules/14](modules/14-scheduler-alerts.md)) — open to all signed-in users under fair-use caps (the alerts v2 design); §9a carve-out

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/schedules` | Create (validated: user-authorized, read-only, quota-checked) |
| GET | `/schedules` | List mine |
| PATCH | `/schedules/{id}` | Partial edit (implemented): send only changed fields — `enabled`, `trigger`, `action`, `delivery_channels`, `quiet_hours`, `daily_limit`; optional `expected_version` → 409 `stale_version`; a new `trigger` or re-enabling recomputes `next_run`; 422 `invalid_trigger` / `unknown_skill` / unknown fields |
| DELETE | `/schedules/{id}` | Delete |
| POST | `/alerts` | Create alert rule: scope `symbol`+`market` (TW/US) **xor** `watchlist_id`; `condition` is a template `{kind, ...}` (`close_price`, `daily_change`, `volume_surge`, `institutional_streak`, `attention_entry`) with decimal params; optional `trigger_mode` (`once`/`once_per_day`/`every_occurrence`), `cooldown_seconds`, `expires_in_days` (default 90, null = never), `explain`; empty channels / null quiet hours / null daily limit inherit preferences. 409 `cap_reached`, 422 `invalid_market`, 404 `watchlist_not_found` |
| GET | `/alerts` | List mine |
| PATCH | `/alerts/{id}` | Partial edit (implemented): send only changed fields — `condition`, `trigger_mode`, `cooldown_seconds`, `expires_in_days` (null = never), `explain`, `channels`, `quiet_hours`, `daily_limit`, `enabled`; optional `expected_version` → 409 `stale_version` on mismatch; scope fields and unknown fields → 422 |
| DELETE | `/alerts/{id}` | Delete |
| POST | `/alerts/proposals` | v2: parse-only rule draft for the chat confirm card (creates nothing) |
| POST | `/notifications/read` | v2: mark read `{ids[]}` or `{all:true}` |
| POST | `/me/channels/line`, `/me/channels/telegram` | v2: start/complete external channel binding |
| GET | `/notifications` | Delivered notifications |
| GET | `/notifications/badge` | Lightweight counts `{unread_notifications, unread_threads, pending_alerts}` — mobile tab-island + bell dots (glyph+count, never color-alone) |
| GET/PUT | `/me/alert-preferences` | Channels, quiet hours, `timezone` (IANA, validated), daily cap, digest (v2 adds `GET`; `PUT` replaces the whole record) |
| GET | `/me/channels` | Which external channels can reach the caller now `{email, push, line, telegram, telegram_available}` |
| POST / DELETE | `/me/channels/line/link`, `/me/channels/line` | Issue a one-time LINE code (`{code, add_friend_url, expires_in_seconds}`) / unlink |
| POST | `/webhooks/resend` | Resend webhook (Svix signature required, else 403): permanent bounce / complaint / Resend suppression → address suppressed |
| GET / POST | `/email/unsubscribe?token=` | One-click email unsubscribe: GET shows a confirmation page, POST unsubscribes (RFC 8058 target). Invalid token → 400 |
| POST | `/channels/line/webhook` | LINE webhook, `X-Line-Signature` required (403 otherwise); binds the sender when a message carries a valid code |
| POST / DELETE | `/me/channels/telegram/link`, `/me/channels/telegram` | Issue a one-time Telegram link (`{url, expires_in_seconds}`) / unlink |
| POST | `/webhooks/telegram` | Bot webhook, `X-Telegram-Bot-Api-Secret-Token` required (403 otherwise); binds the chat on `/start <token>` |
| POST | `/alerts/batch` | 逐檔設定: up to 30 per-stock rules at once `{items:[{symbol,market,condition}], origin_watchlist_id?, trigger_mode?, expires_in_days?}`, all or nothing (409 `cap_reached`; 422 `template_market_mismatch` / duplicate stock; 404 `watchlist_not_found`). `POST /alerts` with a `watchlist_id` refuses a price-level condition (422 `template_scope_mismatch`) |
| GET | `/alerts/symbol-check` | `?symbol&market` → `{checked, exists}`; whether the ticker has price data (a hint, never blocks creation) |
| POST | `/me/devices` | Register native push token `{platform: ios\|android\|web, token}` (idempotent); payload deep-links `{route, symbol?, thread_id?}` ([14](modules/14-scheduler-alerts.md) §4) |
| DELETE | `/me/devices/{token}` | Unregister (logout / rotation) |

---

## 7b. Billing — Paddle ([modules/15](modules/15-payments-paddle.md))

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/billing/plans?country=XX` | tier×interval×BYOK → `price_id` + `price {amount, currency_code}` read from Paddle (minor units, pre-tax; the country override — e.g. TWD for `TW` — when one exists; `price` is `null`, never guessed, if Paddle can't be asked; cached ~10 min) |
| POST | `/billing/checkout` *(409 `already_subscribed` if the caller has a live subscription)* | Create Paddle checkout `{tier, interval, byok}` — server resolves the `price_id` (`payments/plans.py::price_id_for`) and tags the transaction with the caller's user id for webhook attribution; the client never sends a raw `price_id` |
| GET | `/billing/subscription` | Current subscription; **reconciles-on-read** vs Paddle (self-heals a missed webhook) |
| POST | `/billing/portal?purpose=overview\|payment` | Link into Paddle's customer portal, via a portal session: `overview` = portal home (plan, payments, cancel); `payment` = the update-payment-method form (past-due fix). Falls back to the subscription's own management links |
| POST | `/billing/change-plan` | Move the live subscription to another `{tier, interval, byok}` (Paddle's PATCH subscription, prorated immediately); local row + tier refreshed from Paddle at once. 404 `no_subscription`, 409 `same_plan` / `cancel_scheduled`, 400 `unknown_plan` |
| POST | `/billing/resume` | Undo a scheduled cancellation (clears Paddle's `scheduled_change`) **or** restart a paused subscription. 409 `nothing_to_resume` if it is neither |
| GET | `/billing/invoice/{transaction_id}` | Link to Paddle's invoice PDF; only for a transaction in the caller's own history (404 otherwise) |
| GET | `/billing/history` | **Proxies Paddle's transactions API** (no local invoices mirror) |
| POST | `/billing/webhook/paddle` | Paddle events (signature-verified) — **entitlement source of truth** |

BYOK checkbox on the pricing page switches `price_id` to the cheaper BYOK variant. `tier`/
`byok` are set only by the verified webhook, never by the client.

---

## 8. Feedback admin ([modules/13](modules/13-chat-interaction-features.md)) — NOT BUILT

**Not implemented.** Only the minimal `POST /threads/{id}/turns/{tid}/feedback` rating capture
(§2 above) is real. The rolling-7-day-quality-score/alert pipeline these endpoints would serve —
and the endpoints themselves — remain unbuilt:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/feedback/quality` | Rolling 7-day quality per skill |
| GET | `/feedback/alerts` | Pending/resolved alerts |
| GET | `/feedback/recent?skill_name=` | Recent events for review |
| PUT | `/feedback/alerts/{id}/acknowledge` | Admin seen |
| PUT | `/feedback/alerts/{id}/resolve` | Admin fixed |

---

## 9. MCP server ([modules/06](modules/06-interfaces.md))

**Transport:** remote streamable-HTTP. Read-only, FastMCP + Pydantic v2 structured output
(`structuredContent` + JSON text fallback). Every tool carries annotations
(`readOnlyHint:true`, `destructiveHint:false`, `idempotentHint:true`, `openWorldHint:true`,
`title`). Our own tools (not a third-party set); trading-signal tools are never registered;
descriptions are declarative-only (no behavior instructions — Directory prompt-injection rule).
`sponsor` tools return a masked teaser unless the MCP principal is entitled. Errors are MCP
`isError` results with recovery hints, not exceptions.

**Production entry point:** `python -m mcp` (`backend/src/mcp/__main__.py`) — a standalone
process (never wired into the main API process, one more `SERVICE_ROLE` in
`docker-entrypoint.sh`), serving `mcp.composition.build_mcp_http_app`'s ASGI app via `uvicorn`.
That composition root builds the real tool registry through the same
`orchestrator.composition` functions the API app uses. Host/port/allowed-origins/allowed-hosts
are config (`MCP_HOST`/`MCP_PORT`/`MCP_ALLOWED_ORIGINS`/`MCP_ALLOWED_HOSTS`).

### Immediate tools (dedicated) — the real, wired core (5 tools)
| Tool | data_class | Input | Output |
|------|-----------|-------|--------|
| `get_canonical_context` | public | `{symbols[], fields?}` | canonical package |
| `build_conversation_package` | public | `{symbols[], window?}` | conversation package |
| `get_source_health` | public | `{}` | source health (per-capability currentness/status) |
| `get_company_profile` | public | `{id, market}` | company profile card |
| `get_price_quote` | public | `{id, market}` | latest EOD price bar |
| `search_tools` / `execute_tool` | public | `{intent}` / `{id, args}` | long-tail discovery + run |

### Deferred (via `execute_tool` — the long tail exceeds the per-turn schema budget)
`get_chip_flows`, `get_government_bank_flows`, `get_live_quote_snapshot`,
`get_disposition_securities`, `get_finmind_stock_news`, `get_stock_score`,
`get_score_leaderboard`, `run_tag_screener`, `get_tag_catalog`, `get_etf_overlap`,
`get_fund_comparison`, … — `sponsor` ones return a masked teaser if unentitled.
`tests/mcp/test_full_registry_coverage.py` asserts every real tool in the built registry is
reachable over MCP (immediate or via `execute_tool`), not just a spot-checked subset. (The
registry currently builds 100–107 tools depending on which optional dependencies are supplied —
see [modules/03-tools.md](modules/03-tools.md) — 5 of which are immediate, the rest deferred.)

### Resources
| URI | Purpose |
|-----|---------|
| `canonical://{symbol}` | Canonical package as a resource |
| `health://sources` | Source-health snapshot |

---

## Cross-references
- Schema behind these endpoints: [database-schema.md](database-schema.md)
- Rules enforced on every endpoint: [governance.md](governance.md)
- System overview: [architecture.md](architecture.md)
