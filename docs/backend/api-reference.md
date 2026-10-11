# API Reference

Consolidated reference for all interfaces: **REST**, **SSE**, and **MCP**. Endpoints are
served by `backend/src/api/` (FastAPI) and `backend/src/mcp/` (FastMCP). Per-module detail
is linked inline. All routes are **read-only w.r.t. market data**; the only writes are
product/app state (threads, memory, schedules…).

**Conventions**
- Base path `/api/v1`. Auth via session cookie or `Authorization: Bearer`.
- Anonymous allowed for public market/context reads; AI chat requires sign-in.
- Errors: JSON `{code, message, retryable}` for every error: ones the app raises (`ApiError`),
  request validation (422, first problem with its location, e.g. `query.symbol`), and the
  framework's own (unknown path `not_found`, wrong method `method_not_allowed` with the `Allow`
  header). Unentitled AI features return a **teaser envelope**
  `{locked, required_tier, preview}` (HTTP 200), never 401 — see
  [modules/12](modules/12-multi-user-tiers.md).
- Rate limits (abuse protection, same for every tier): data-read routes 600/min per signed-in user
  or 240/min per anonymous address; sign-in endpoints (`/auth/oauth/*`, `/auth/refresh`) 30/min per
  address; chat turns and voice have their own limits. Over the limit: HTTP 429, code
  `rate_limited`, `retryable: true`, with `Retry-After`. Set `TRUSTED_PROXY_COUNT` to the number
  of your own reverse proxies in front of the API so the caller is read from `X-Forwarded-For`
  that many hops from the right (default 0 = header ignored, since clients can forge it).
- `Idempotency-Key` honored on turn-creating POSTs.
- **Expired access token on a public route.** Routes that allow anonymous callers still answer a
  caller whose access token has expired (as anonymous, so a logged-out visitor with a stale cookie
  works) but add the response header **`X-Auth-Expired: 1`** (`api/auth_expired.py`, exposed through
  CORS). The web client treats that like a 401: it refreshes the session and retries once
  (`fetchWithRefresh`), and keeps the token fresh in the background while signed in.
- **Response typing.** Data routes return the tool's output as a plain JSON object
  (`dict[str, object]`); the OpenAPI schema therefore does not describe their bodies. The contract
  lives in each tool's Pydantic output model and the web client's Zod schemas (`web/src/types/`).
- **Caching is not implemented yet.** The design (an `as_of` on every cacheable read, a strong
  `ETag`, `If-None-Match` → `304`, `Cache-Control: private, max-age=0,
  stale-while-revalidate=<class boundary>`) is **not** in the API code today: no route sets `ETag`
  or `Cache-Control`, and the web client relies on TanStack Query's own staleness instead. What does
  exist: a **`data.updated`** SSE event (`{capability, market, as_of, scope}`) fires when a
  derived-compute batch lands so clients invalidate precisely instead of waiting a TTL, and the
  `quote` SSE payload carries per-tick `as_of` + `market_open`. See
  [modules/16](modules/16-infrastructure.md) §3a for the design.
- **List-row quotes.** Any list of symbols (leaderboards, watchlists, rankings, the screener) reads
  `GET /market/quotes` once per list: name, latest close, **daily** change and change %, volume and
  a sparkline for up to 20 symbols. Never call it per row.
- **Units.** `revenue_yoy` is a ratio (`0.05` = +5%); `dividend_yield` and `*_pct` fields are
  already percentages. Check the field's name and the tool's output model before formatting.

---

## 1. Agent & streaming ([modules/07](modules/07-conversation-streaming.md))

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/agent/query` | Start a turn (thread_id optional → auto-creates); returns SSE. **Real request body today is `{message, thread_id?, web_search?, deep_research?, locale?, attachments?}`** (`api/routers/agent.py`'s `AgentQueryRequest`) + the `Idempotency-Key` header — the classifier alone decides framework/skills routing server-side. `web_search` toggles web search for the turn and `attachments` are processed via `process_attachments()`, both wired through to `start_turn_stream(web_search_enabled=body.web_search, ...)`. A per-turn `model` override is real too, but lives on the separate `PATCH /threads/{id}` route (`model_override`, below), not on this body. `deep_research:true` runs Pro-only cited web research instead of a normal reply ([24](modules/24-deep-research.md)): `403 feature_not_entitled` for non-Pro, `429 deep_research_limit_reached` after 5 reports/UTC day (`regenerate`/`edit` bodies accept `deep_research?` too; omitted = re-run the way the original request ran, stored as `turns.mode`). **Not yet built:** `plan` mode, `skill_hint`. Personality resolves from `threads.personality_override` else `users.personality_id` ([modules/23](modules/23-agent-personality.md), real, wired into every turn via `PersonalityContext`) — set via `PATCH /me {personality_id}` (user default); the per-chat pin is `threads.personality_override`, set by `PATCH /threads/{id}` or by the optional `personality` field of this body (`"default"` clears it; unknown id → `422 unknown_personality`). Each turn emits an SSE `personality.used {personality}` and stores the id in `turns.usage.personality` |
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

> **Implementation status.** The kinds are defined once, in `backend/src/events/kinds.py`
> (`EventKind`); a test keeps that list equal to the web client's `SSE_EVENT_KINDS`
> (`web/src/types/sse.ts`).
> **Emitted today (chat turn stream):** `turn.start`, `route.decided`, `agent.spawned`,
> `agent.progress`, `agent.done`, `tool.start`, `tool.result`, `thinking.delta`,
> `web_search.result`, `rich_card`, `synthesis.start`, `usage`, `memory.used`, `personality.used`,
> `pet.leveled`, `pet.pull_earned`, `steering.accepted`, `message`, `message.delta`, `turn.end`.
> `data.updated` is the separate `/updates/stream` feed; `alert.fired` and `schedule.*` are internal
> scheduler events.
> **Not emitted:** `activity`, `tool.proposed`, `tool.started`, `tool.locked`, `tool.blocked`,
> `follow_up`, `caveat`, `pet.reaction` (a masked `sponsor` tool result comes back inline in the
> `tool.result` payload rather than as a dedicated `tool.locked` frame). See
> [modules/07](modules/07-conversation-streaming.md) for the full designed vocabulary.

---

## 2. Threads / history ([modules/13](modules/13-chat-interaction-features.md))

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/threads` | Create thread |
| GET | `/threads?include_archived=` | List mine. **Real query surface today is just `include_archived: bool` (default `false`)** (`api/routers/threads.py`'s `list_threads`) — `sort`/`filter`/`q`/pagination are not yet implemented as query params; use `GET /threads/search?q=` (below) for a text query |
| GET | `/threads/{id}` | Thread + history |
| PATCH | `/threads/{id}` | Rename title, star/pin, archive, tag, set per-thread `model_override` / `personality_override` (`"default"` clears) |
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
| GET | `/search?q=&market=TW` | Symbol type-ahead. Two backing tools, never merged: `market=TW` → `get_symbol_directory_search` (a scan over the ingested `TaiwanStockInfo` roster, stocks + ETFs, ranked symbol-prefix > name-prefix > substring); US/GB/JP/EU → `get_symbol_search` (yfinance). Any other market returns HTTP 501 `not_supported` | public |
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
| GET | `/stock/technical-indicators?symbol=&market=&start_date=&end_date=` | Indicator series (MA/KD/MACD/RSI/Bollinger/volume) behind the K-line chart; lives under `/stock/*`, **not** `/analytics/{symbol}/indicators` | public |
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
| GET | `/market/advance-decline` | 漲跌家數 + >5%/3–5% buckets (`get_advance_decline`) | public |
| GET | `/market/index-contribution?index=TAIEX` | per-stock 點貢獻 (`get_index_contribution`) | public |
| GET | `/market/institutional-ranking` | top net-buy/sell stocks (whole-market 三大法人) | public |
| GET | `/market/margin-maintenance` | 大盤融資維持率 risk (`get_margin_maintenance`) | public |
| GET | `/market/day-trading` | 當沖熱門/比重排行 (`get_day_trading`) | public |
| GET | `/market/etf-money-flow` | cross-ETF 資金流向 (`get_etf_money_flow`) | Premium+ (mosaic) |
| GET | `/market/industry-flow` | 產業鏈資金流 (`get_industry_money_flow`) | Premium+ (mosaic) |
| GET | `/derivatives/{contract}` | 期/選 OI · P/C · VIX · 三大法人 · 大額交易人 (`get_futures_options_daily`/`get_put_call_ratio`/`get_taiex_vix`/`get_derivatives_institutional`) | public |
| GET | `/macro` | 匯率/利率/債殖利率/黃金/原油 + Fear-Greed (`get_macro_indicators`) | public |
| GET | `/macro/business-indicator` | 景氣對策信號 燈號 (`get_business_indicator`) | public |

> **Implementation status.** The rows above are real, wired routes (`api/routers/market_rest/`),
> and `/market/advance-decline` and `/market/index-contribution` are built too (the 每日焦點
> breadth card). **`/market/overview` and `/market/sectors` are not built** (no `get_market_context`
> / `get_sector_indices` tool exists), so they are not listed. The full, generated list of every
> route (about 75 under `/market/*`, plus the whole `/stock/*` family) is the **Route index** at
> the end of this document.

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
| GET/PATCH | `/pets/settings` | `{hint_enabled, companion_visible}`: pet-flavoured replies (default off); companion avatar in chat (default on). PATCH takes either |

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
| GET | `/watchlists?market=TW` | My groups for a market tab (ordered); omit `market` → all. The first read for a user seeds the default preset groups (`WatchlistStore.seed_default_presets`, from `config/watchlist_presets.py`), so a new user's rail is not cold-empty; it never re-seeds over a user's own edits or deletions (so this GET can write on a first load). Each group's item list is returned inline (no separate `item_count` field on the group view today) |
| POST | `/watchlists` | Create group `{name, market, asset_class?}` (`asset_class` defaults to `stock`; appended, lexorank end key) |
| PATCH | `/watchlists/{id}` | Rename `{name}`, set `{color}` (`chart-1`..`chart-6`, or `"none"` to clear; others → 422) and/or reorder `{after_id\|before_id}`; `If-Match: <version>` |
| DELETE | `/watchlists/{id}` | Soft-delete group + cascade items |
| POST | `/watchlists/reorder` | Bulk reorder `{ordered_ids[]}` — full-array fallback for a big reshuffle |
| GET | `/watchlists/{id}/items` | Items in a group (usually returned inline by the group GET) |
| PUT | `/watchlists/{id}/items/{symbol}` | **Star toggle — add** (idempotent — a repeat add returns the existing row, never a duplicate); body `{note?}`. No `after_id` on this route — a newly-added item lands at the end of the group's sort order; use the item `PATCH` below to reorder afterward |
| DELETE | `/watchlists/{id}/items/{symbol}` | **Star toggle — remove** (idempotent; 204 regardless) |
| PATCH | `/watchlists/{id}/items/{item_id}` | Set/clear `{note, clear_note?}` — note-only; **no reorder-within-group on this route today** (`PatchItemRequest` carries no `after_id`/`before_id`) |
| POST | `/watchlists/{id}/items/{item_id}/move` | Move item to another group `{target_watchlist_id, after_id?}` |

**Batch quotes** — the rail reads its symbols through `GET /market/quotes?symbols=…&market=…` once
per tab (name, latest close, daily change %, sparkline; ≤20 symbols per call, chunked by the client)
and may overlay `GET /quote/stream?symbols=…` (live-ish, Premium+). Never one call per row.

---

## 6. Accounts, tiers & usage ([modules/12](modules/12-multi-user-tiers.md))

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/me` | Profile + tier + entitlements |
| PATCH | `/me` | Update profile (display_name, birthday, phone+country code, nationality, **personality_id** — agent-voice preset, [modules/23](modules/23-agent-personality.md)) |
| GET | `/personality/presets` | List agent-voice presets (id, names, description, default, version - never the prompt text) for the Settings picker |
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
| POST | `/notifications/read` | v2: mark read `{ids[]}` or `{all:true}` |
| GET | `/notifications` | Delivered notifications |
| GET | `/notifications/badge` | Lightweight counts `{unread_notifications, unread_threads, pending_alerts}` — mobile tab-island + bell dots (glyph+count, never color-alone) |
| GET/PUT | `/me/alert-preferences` | Channels, quiet hours, `timezone` (IANA, validated), daily cap, digest (v2 adds `GET`; `PUT` replaces the whole record) |
| GET | `/me/channels` | Which external channels can reach the caller now `{email, push, line, telegram, telegram_available}` |
| POST / DELETE | `/me/channels/line/link` (issue code), `/me/channels/line` (unlink) | Issue a one-time LINE code (`{code, add_friend_url, expires_in_seconds}`) / unlink |
| POST | `/webhooks/resend` | Resend webhook (Svix signature required, else 403): permanent bounce / complaint / Resend suppression → address suppressed |
| GET / POST | `/email/unsubscribe?token=` | One-click email unsubscribe: GET shows a confirmation page, POST unsubscribes (RFC 8058 target). Invalid token → 400 |
| POST | `/channels/line/webhook` | LINE webhook, `X-Line-Signature` required (403 otherwise); binds the sender when a message carries a valid code |
| POST / DELETE | `/me/channels/telegram/link` (issue link), `/me/channels/telegram` (unlink) | Issue a one-time Telegram link (`{url, expires_in_seconds}`) / unlink |
| POST | `/webhooks/telegram` | Bot webhook, `X-Telegram-Bot-Api-Secret-Token` required (403 otherwise); binds the chat on `/start <token>` |
| POST | `/alerts/batch` | 逐檔設定: up to 30 per-stock rules at once `{items:[{symbol,market,condition}], origin_watchlist_id?, trigger_mode?, expires_in_days?}`, all or nothing (409 `cap_reached`; 422 `template_market_mismatch` / duplicate stock; 404 `watchlist_not_found`). `POST /alerts` with a `watchlist_id` refuses a price-level condition (422 `template_scope_mismatch`) |
| GET | `/alerts/symbol-check` | `?symbol&market` → `{checked, exists}`; whether the ticker has price data (a hint, never blocks creation) |
| POST | `/me/devices` | Register native push token `{platform: ios\|android\|web, token}` (idempotent); payload deep-links `{route, symbol?, thread_id?}` ([14](modules/14-scheduler-alerts.md) §4) |
| DELETE | `/me/devices/{token}` | Unregister (logout / rotation) |

---

## 7b. Billing — Paddle ([modules/15](modules/15-payments-paddle.md))

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/billing/plans?country=XX` | tier×interval×BYOK → `price_id` + `price {amount, currency_code}` read from Paddle (minor units, pre-tax; the country override — e.g. TWD for `TW` — when one exists; `price` is `null`, never guessed, if Paddle can't be asked; cached ~10 min). Also `tiers`: Free/Premium/Pro side by side (chat limit, req/min, sponsor data, features, deep-research limit), projected from `accounts/entitlements` so the pricing page cannot drift from enforcement |
| POST | `/billing/checkout` *(409 `already_subscribed` if the caller has a live subscription)* | Create Paddle checkout `{tier, interval, byok}` — server resolves the `price_id` (`payments/plans.py::price_id_for`) and tags the transaction with the caller's user id for webhook attribution; the client never sends a raw `price_id` |
| GET | `/billing/subscription` | Current subscription; **reconciles-on-read** vs Paddle (self-heals a missed webhook) |
| POST | `/billing/portal?purpose=overview\|payment` | Link into Paddle's customer portal, via a portal session: `overview` = portal home (plan, payments, cancel); `payment` = the update-payment-method form (past-due fix). Falls back to the subscription's own management links |
| POST | `/billing/change-plan` | Move the live subscription to another `{tier, interval, byok}` (Paddle's PATCH subscription, prorated immediately); local row + tier refreshed from Paddle at once. 404 `no_subscription`, 409 `same_plan` / `cancel_scheduled`, 400 `unknown_plan` |
| POST | `/billing/sync` | Recover a checkout whose webhook never arrived: body `{transaction_id}`; adopts the subscription only if that Paddle transaction was opened for the caller. Returns the subscription (or `null` while the payment has none yet) |
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

## 10. Route index (generated)

Every REST route the code serves, grouped by router, with its auth requirement, the tool that backs
it and that tool's data class. **Generated, do not edit by hand:** run
`cd backend && UPDATE_API_REFERENCE=1 uv run pytest tests/api/test_api_reference_sync.py` after adding,
removing or renaming a route. `tests/api/test_api_reference_sync.py` fails when a route is missing from this table, so the
list cannot drift from the code. The hand-written sections above add the behaviour and caveats a
table cannot carry.

<!-- ROUTES:BEGIN (generated by tests/api/test_api_reference_sync.py) -->

215 routes. **Auth**: `user` = sign-in required (401 otherwise); `optional` = anonymous allowed, the caller's tier applies when signed in; `none` = no auth dependency (probes, webhooks verified by signature, OAuth). **Tier** is the backing tool's data class: `sponsor` data is masked for unentitled callers with the HTTP 200 teaser envelope.


#### Context, quotes & source health

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| GET | `/api/v1/context/{package}` | optional | `build_conversation_package`, `get_canonical_context` | public | The canonical (`canonical`) or AI-safe conversation (`conversation`) package for one symbol. |
| GET | `/api/v1/search` | optional | `get_symbol_directory_search`, `get_symbol_search` | public | Symbol type-ahead over the free-text search capability. |
| GET | `/api/v1/quote/stream` | optional | — | — | Live-ish quote feed (SSE) via the shared demand-driven poller. |
| GET | `/api/v1/updates/stream` | optional | — | — | Cache-invalidation feed (SSE): `data.updated` frames when a derived-compute batch lands. |

#### Quotes & live streams

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| GET | `/api/v1/health` | optional | `get_source_health` | public | Source-health view for the given symbols (currentness + which upstreams are OK/stale). |
| GET | `/api/v1/sources` | optional | `get_source_matrix` | public | The adapter/capability matrix — which `(capability, market)` pairs each source declares. |
| GET | `/api/v1/quote` | optional | `get_price_quote` | public | Latest end-of-day price bar per symbol (one-shot; the live-ish feed is a separate SSE). |

#### Market, stock-detail, derivatives & macro data

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| GET | `/api/v1/market/index-strip` | optional | `get_market_index_strip` | public | TW-tab ticker-strip public items (加權指數/櫃買指數/美元/黃金/原油) |
| GET | `/api/v1/market/us-index-strip` | optional | `get_us_market_index_strip` | public | US-tab ticker-strip public items (S&P500/Dow Jones/Nasdaq/SOX/VIX/US 10Y yield/Fed funds rate) |
| GET | `/api/v1/market/tick-snapshot` | optional | `get_tick_snapshot` | sponsor | One bounded intraday tick-level snapshot for one stock/ETF or index code (Sponsor-tier) |
| GET | `/api/v1/market/futures-snapshot` | optional | `get_futures_snapshot` | sponsor | One bounded intraday futures-contract snapshot (Sponsor-tier) |
| GET | `/api/v1/market/institutional-ranking` | optional | `get_institutional_flow_total_market` | public | Whole-market three-major-institutional-investor buy/sell totals by category. |
| GET | `/api/v1/market/margin-maintenance` | optional | `get_margin_maintenance` | sponsor | Whole-market margin-maintenance-ratio (融資維持率) risk gauge (Sponsor-tier). |
| GET | `/api/v1/market/day-trading` | optional | `get_day_trading` | public | Day-trading (當沖) eligibility + volume/value for one stock. |
| GET | `/api/v1/market/dividend-schedule` | optional | `get_dividend_schedule` | public | Announced dividend/ex-dividend (除權息) schedule for one stock/ETF symbol. |
| GET | `/api/v1/market/etf-money-flow` | optional | `get_etf_money_flow` | sponsor | Cross-ETF 資金流向 over a bounded date range (Sponsor-tier). |
| GET | `/api/v1/market/daily-signal` | optional | `get_daily_signal` | sponsor | 今日訊號 — whole-market, single-day active-ETF operations signal aggregated across every active ETF (Sponsor-tier). |
| GET | `/api/v1/market/industry-flow` | optional | `get_industry_money_flow` | sponsor | Whole-market industry-chain money-flow (產業鏈資金流) aggregate (Sponsor-tier). |
| GET | `/api/v1/market/margin-short-balance` | optional | `get_margin_short_total_market` | public | Whole-market margin-purchase/short-sale total by category. |
| GET | `/api/v1/market/advance-decline` | optional | `get_advance_decline` | public | Whole-market advance/decline breadth for one trading day (defaults to the latest available day) — the backing tool's input is a single `trade_date`,… |
| GET | `/api/v1/market/order-book-stats` | optional | `get_order_book_stats` | public | Whole-exchange 5-second order-book/trade aggregate (委買委賣統計) — upstream is single-day per request, so only `start_date`/`end_date` (via `_market_args`… |
| GET | `/api/v1/market/price-momentum` | optional | `get_price_momentum` | public | Whole-market top-N gainers/losers by pure close-to-close percentage change over a chosen lookback period (day/week/month trading days) — single-facto… |
| GET | `/api/v1/market/us-rankings` | optional | `get_us_market_rankings` | public | US-only 排行榜 multi-tab ranking: `trending` (Yahoo's own most-active screen), `market_cap`/ `revenue`/`roe`/`volume` (a bounded Yahoo screener query so… |
| GET | `/api/v1/market/tw-rankings` | optional | `get_tw_market_rankings` | public | TW-only 排行榜 multi-tab ranking (6 of its 7 tabs — 大戶買賣 reuses `/market/holder-accumulation` verbatim instead of this route): dealer/trust/foreign cons… |
| GET | `/api/v1/market/holder-accumulation` | optional | `get_holder_accumulation` | sponsor | 大戶加碼股 — whole-market ranking of which stocks grew a chosen large-holder tier (>=200/400/800/1,000張) share-of-custody the most over 1-4 weekly 集保戶股權分散… |
| GET | `/api/v1/market/index-contribution` | optional | `get_index_contribution` | sponsor | APPROXIMATE per-symbol index-contribution estimate for one trading day (market-cap weight x price change) — not TWSE's official float-adjusted figure… |
| GET | `/api/v1/market/sector-heatmap` | optional | `get_sector_heatmap` | public | Whole-market industry/sector heatmap (產業別漲跌熱力圖) for one trading day (defaults to the latest available day): each industry's average percentage change… |
| GET | `/api/v1/market/stock-heatmap` | optional | `get_stock_heatmap` | public | Whole-market flat per-stock heatmap (股票熱力圖) for one trading day (defaults to the latest available day): the top-ranked stocks' own percentage change… |
| GET | `/api/v1/market/sector-constituents` | optional | `get_sector_constituents` | public | Per-stock drill-down (close-to-close % change, close, volume, turnover) for one `/market/sector-heatmap` industry tile, or every symbol ranked togeth… |
| GET | `/api/v1/market/sector-rotation` | optional | `get_sector_rotation` | public | Daily industry ranking by net institutional flow over the last 10 trading days ending at `end_date` (defaults to the latest available day) — the data… |
| GET | `/api/v1/market/sector-rotation-rrg` | optional | `get_sector_rotation_rrg` | public | Real weekly Relative Rotation Graph (RS-Ratio x RS-Momentum, JdK construction) for TWSE's published industry sub-indices against TAIEX, ending at `en… |
| GET | `/api/v1/market/etf-list` | optional | `get_etf_list` | public | Whole-market Taiwan-listed ETF list for one trading day (defaults to the latest available day): price/change/volume/1-week-return, AUM, expense-ratio… |
| GET | `/api/v1/market/dividend-calendar` | optional | `get_dividend_calendar` | public | 股息日曆 — every Taiwan-listed ETF's 除息日/最後購買日/股利發放日 landing within one requested calendar month (defaults to the current month). |
| GET | `/api/v1/market/big-holder-accumulation` | optional | `get_shareholder_dispersion` | sponsor | 股權分散 shareholding-level distribution (千張大戶比率) for one stock — a chip-concentration signal (Sponsor-tier). |
| GET | `/api/v1/market/active-etf-report` | optional | `get_active_etf_report` | sponsor | 主動式ETF 操作日報 for one active-management ETF — required `symbol`, plus an optional single `trade_date` (the backing tool's own field name; not a start/e… |
| GET | `/api/v1/market/international-index` | optional | `get_international_stock` | public | FinMind's own roster-info + EOD-price bundle for one US/GB/EU/JP quote symbol — a distinct dataset family from the yfinance-primary `price_eod` path… |
| GET | `/api/v1/market/material-announcements` | optional | `get_material_announcements` | public | Today's MOPS material-announcement (重大訊息) disclosures for one stock. |
| GET | `/api/v1/market/material-announcement-feed` | optional | `get_material_announcement_feed` | public | 重大訊息 whole-market feed for one day (defaults to the latest day MOPS ingest has captured), optionally filtered to one derived category bucket, with a… |
| GET | `/api/v1/market/insider-transfer-declarations` | optional | `get_insider_transfer_declarations` | public | 內部人股權轉讓事前申報 (insider advance-transfer-plan declaration) whole-market feed for one day (defaults to the latest day MOPS ingest has captured) — `t187ap… |
| GET | `/api/v1/market/disposition-board` | optional | `get_disposition_board` | sponsor | 處置王-style disposition board: 風險股/處置中/即將出關 buckets, read from the ingest-owned `attention_stocks`/`disposition_events` partitions plus already-ingeste… |
| GET | `/api/v1/market/tag-catalog` | optional | `get_tag_catalog` | public | The discoverable tap-tile screener tag list, grouped by category with bilingual labels — `get_tag_catalog` takes no input, so this route dispatches w… |
| GET | `/api/v1/market/tag-screener` | optional | `run_tag_screener` | public | Tap-tile tag screener over the nightly-computed flag partition. |
| GET | `/api/v1/market/symbol-info` | optional | `get_symbol_info` | public | Batched symbol->name (+ industry/security-type) lookup, e.g. |
| GET | `/api/v1/market/price-history` | optional | `get_price_history` | public | A bounded trailing-day OHLCV window for up to 20 symbols — the data behind a sparkline or a client-computed %-change, for any bare-symbol list (leade… |
| GET | `/api/v1/market/quotes` | optional | `get_symbol_quotes` | public | The list-row view for up to 20 symbols: name, latest close, DAILY change/change %, volume and a sparkline, in one call. |
| GET | `/api/v1/derivatives/taiex-vix` | optional | `get_taiex_vix` | sponsor | 臺指選擇權波動率指數 (TAIEX option VIX) reading series — whole-market, Sponsor-tier. |
| GET | `/api/v1/derivatives/{contract}` | optional | `get_derivatives_institutional`, `get_futures_options_daily`, `get_put_call_ratio` | public/sponsor | 期/選 daily OI/price, put/call positioning, and institutional/large-trader positioning for one contract code — descriptive positioning context, not a d… |
| GET | `/api/v1/macro` | optional | `get_macro_indicators` | sponsor | 匯率/利率/黃金/原油/美債殖利率/Fear-Greed macro-context report (Sponsor-tier). |
| GET | `/api/v1/macro/business-indicator` | optional | `get_business_indicator` | sponsor | Monthly 景氣對策信號 business-climate indicator (Sponsor-tier). |
| GET | `/api/v1/market/macro-calendar` | optional | `get_macro_calendar` | public | US/global macro-release events (GDP/CPI/PCE/PMI/NFP/FOMC) with consensus/prior/actual |
| GET | `/api/v1/market/news-feed` | optional | `get_news_feed` | public | 即時新聞 — near-real-time news aggregated from financial RSS + optional Finnhub |
| GET | `/api/v1/market/ptt-board` | optional | `get_ptt_board` | public | PTT — one board's index-listing (title/author/push-count/post-tag/posted_date) |
| GET | `/api/v1/stock/company-profile` | optional | `get_company_profile` | public | AssetHeader + 基本資料's 個股資訊卡/財務快照 — the composite profile card |
| GET | `/api/v1/stock/insider-shareholding` | optional | `get_insider_shareholding` | public | 基本資料's 內部人/董監持股·質押比 — MOPS 董監事持股餘額 |
| GET | `/api/v1/stock/insider-transfer-declarations` | optional | `get_insider_transfer_declarations` | public | 基本資料's 董監事持股 card, 持股轉讓 sub-tab — 內部人股權轉讓事前申報, filtered to one symbol server-side (the underlying `get_insider_transfer_declarations` tool is a whole… |
| GET | `/api/v1/stock/esg-score` | optional | `get_esg_score` | public | 基本資料's ESG風險卡 |
| GET | `/api/v1/stock/disposition-securities` | optional | `get_disposition_securities` | sponsor | 基本資料's 處置/注意 banner — 處置有價證券 announcement rows |
| GET | `/api/v1/stock/dividend-result` | optional | `get_dividend_result` | public | 財務分析's 股利政策 (settled-result half) — 除權除息結果 |
| GET | `/api/v1/stock/dividend-policy-summary` | optional | `get_dividend_policy_summary` | public | 財務分析's 股利政策 card — the one-call composite report (announced events, per-year cash+stock totals, 現金殖利率/配息率/填息成功率 stats, per-event fill results) |
| GET | `/api/v1/stock/month-revenue` | optional | `get_month_revenue` | public | 財務分析's 月營收趨勢 — 月營收 filing + pre-computed MoM/YoY |
| GET | `/api/v1/stock/financial-statement` | optional | `get_financial_statement` | public | 財務分析's 財務結構/獲利趨勢 sub-tabs (損益表/資產負債表/現金流) — one statement type per call |
| GET | `/api/v1/stock/institutional-flow` | optional | `get_institutional_flow` | public | 籌碼分析's 法人 sub-tab — per-symbol 三大法人買賣 + pre-computed rolling net |
| GET | `/api/v1/stock/margin-short` | optional | `get_margin_short` | public | 籌碼分析's 融資券 sub-tab — per-symbol 融資融劵 balance |
| GET | `/api/v1/stock/securities-lending` | optional | `get_securities_lending` | public | 籌碼分析's 借券 sub-tab — 借券成交 rows |
| GET | `/api/v1/stock/chip-flows` | optional | `get_chip_flows` | sponsor | 籌碼分析's 分點 sub-tab — 分點/broker-level trading rows |
| GET | `/api/v1/stock/government-bank-flows` | optional | `get_government_bank_flows` | sponsor | 籌碼分析's 摘要 sub-tab's 4th 法人籤碼 category — 八大行庫 (eight government-bank) buy/sell rows |
| GET | `/api/v1/stock/valuation-ratios` | optional | `get_valuation_ratios` | public | 研究圖表's 估值河流 — daily PER/PBR/殖利率 + pre-computed band |
| GET | `/api/v1/stock/analyst-consensus` | optional | `get_analyst_consensus` | public | 研究圖表's 分析師深度 — target-price/rating consensus |
| GET | `/api/v1/stock/sec-filings` | optional | `get_sec_filings` | public | The 新聞公告 tab's SEC filings list — most-recent 10-K/10-Q/8-K/etc. |
| GET | `/api/v1/stock/live-price-quote` | optional | `get_live_price_quote` | public | `AssetHeader`'s one-shot price read for US symbols |
| GET | `/api/v1/stock/calendar-events-feed` | optional | `get_calendar_events_feed` | public | The US 總覽 tab's upcoming-events calendar — earnings/IPO/economic calendars bundled from `yfinance.Calendars` |
| GET | `/api/v1/stock/peers` | optional | `get_stock_peers` | public | The 產業同業 tab's sector/industry peer list — resolves the symbol's own classification then fetches its peers |
| GET | `/api/v1/stock/calendar-events` | optional | `get_calendar_events` | sponsor | 研究圖表's 個股事件calendar — feed-sourced 除權息/CB events |
| GET | `/api/v1/stock/finmind-news` | optional | `get_finmind_stock_news` | public | 相關新聞's 財經新聞 sub-tab — FinMind's own per-symbol news dataset (single trading day per request upstream) |
| GET | `/api/v1/stock/foreign-shareholding` | optional | `get_foreign_shareholding` | public | 籌碼分析's 三大法人 column, 持股 sub-tab — daily 外資持股比 (foreign-ownership ratio/ limit) |
| GET | `/api/v1/stock/day-trading` | optional | `get_day_trading` | public | 籌碼分析's 融資券 column, 當沖 sub-tab — day-trading eligibility + volume/value |
| GET | `/api/v1/stock/technical-indicators` | optional | `get_technical_indicators` | public | 技術分析's K線 chart data source — MA/Bollinger/KD/MACD series, 支撐/壓力 levels, and candlestick-pattern hits over a date range |
| GET | `/api/v1/stock/shareholder-dispersion` | optional | `get_shareholder_dispersion` | sponsor | 技術分析/籌碼分析's 散戶 副圖 overlay — 股東人數/千張大戶比率 concentration series |
| GET | `/api/v1/stock/etfs-holding-stock` | optional | `get_etfs_holding_stock` | sponsor | 追蹤 tab's ETF持有 card — every active-management ETF holding one stock, with 持有張數排行/權重排行/建清倉 sub-views |
| GET | `/api/v1/stock/live-quote` | optional | `get_live_quote_snapshot` | public | 即時 tab's 五檔 ladder — one bounded TWSE MIS best-5 bid/ask + last-trade snapshot |
| GET | `/api/v1/stock/price-limit` | optional | `get_price_limit` | public | 看盤 tab's 詳細 stats row — daily 參考/漲停/跌停 price |
| GET | `/api/v1/stock/intraday-kbar` | optional | `get_intraday_kbar` | sponsor | 即時 tab's 分K chart — minute-bar series for one trading day |
| GET | `/api/v1/stock/price-tick` | optional | `get_price_tick` | sponsor | 即時 tab's 逐筆 list — tick-by-tick trade history for one trading day |
| GET | `/api/v1/stock/ptt-search` | optional | `get_ptt_stock_search` | public | 相關新聞's PTT sub-tab — best-effort per-symbol search of Stock/Tech_Job/Warrant board posts (matched against post BODY text, not just the title) |

#### Analytics, scoring & screener

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| GET | `/api/v1/analytics/{symbol}/score` | optional | `get_stock_score` | public | The stock-detail-page read: the cached composite + band + five-factor breakdown for one symbol, or `found=False` if today's batch has not yet compute… |
| GET | `/api/v1/analytics/{symbol}/summary` | optional | `get_stock_summary` | sponsor | The stock-detail-page AI-summary read: the cached (or freshly generated) headline + narrative + SWOT grounded in today's cached composite score. |
| GET | `/api/v1/analytics/rankings` | optional | `get_score_leaderboard` | public | The whole-TW-universe leaderboard: top-N strongest (`direction=bullish`) or weakest (`direction=bearish`) cached composite scores — a sort-and-slice… |
| GET | `/api/v1/analytics/{symbol}/trend-score` | optional | `get_trend_score` | public | The 趨勢評分 tab read: the six-dimension trend-score composite (five cache-backed, one computed live for the requested symbol) plus trailing history for… |
| GET | `/api/v1/analytics/screener/factors` | optional | `get_factor_catalog` | public | The per-market screener factor/signal catalog the condition builder discovers from (README.md §2.1) — `get_factor_catalog`. |
| GET | `/api/v1/analytics/screener/presets` | optional | `get_screener_presets` | public | Named starting-point condition sets for the `PresetLibrary` (README.md §3) — `get_screener_presets`. |
| POST | `/api/v1/analytics/screener/run` | optional | `run_screener` | public | Run the gates->setup screener funnel (README.md §1/§5) — `run_screener`. |
| GET | `/api/v1/analytics/screener/screens` | user | — | — | `SavedScreens`'s list read (README.md §6) — every screen the caller has saved for this market, most-recently-updated first. |
| POST | `/api/v1/analytics/screener/screens` | user | — | — | Save the builder's current state as a new named screen (README.md §6). |
| PATCH | `/api/v1/analytics/screener/screens/{screen_id}` | user | — | — | Rename and/or replace a saved screen's conditions in place — the "dirty -> update" path (README.md §6), distinct from creating a new screen via `POST… |
| DELETE | `/api/v1/analytics/screener/screens/{screen_id}` | user | — | — | Idempotent delete — deleting a screen that isn't there (already gone, or never the caller's) is a no-op, never a 404, matching `DELETE /watchlists/{i… |

#### Themes & supply chain

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| GET | `/api/v1/themes` | optional | `get_themes` | public | The discovery-level theme catalog (screener README.md §6 主題 Themes sub-tab) — every active theme with its member count. |
| GET | `/api/v1/themes/{key}` | optional | `get_theme_members` | public | One theme's identity + its member symbols, each with sub-industry/confidence/source. |
| GET | `/api/v1/themes/{key}/tiers` | optional | `get_theme_tiers` | public | One theme's sub-industries grouped into upstream/midstream/downstream, each assignment marked curated (editorial) or heuristic (automatically classif… |
| GET | `/api/v1/symbols/{symbol}/themes` | optional | `get_symbol_themes` | public | The reverse lookup: every theme one symbol belongs to. |
| GET | `/api/v1/themes/{key}/tier-heatmap` | optional | `get_theme_tier_heatmap` | public | 個股 > 產業分析's 產業鏈 tier-tile view — a theme's upstream/midstream/downstream sub-industry tiles, each colored by that sub-industry's own members' real av… |
| GET | `/api/v1/symbols/{symbol}/supply-chain` | optional | `get_supply_chain_edges` | public | 個股 > 產業分析's 供應鏈關係圖 card — every supply/customer/competitor edge involving one symbol, from either side. |

#### Funds, analysts & ETF overlap

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| GET | `/api/v1/funds/{symbol}/options-chain` | optional | `get_options_chain` | public | Option chain (calls + puts, one expiry) for one non-TW symbol. |
| GET | `/api/v1/funds/{symbol}/institutional-holders` | optional | `get_institutional_holders` | public | Institutional + mutual-fund holder rows for one non-TW symbol. |
| GET | `/api/v1/funds/{symbol}/insider-activity` | optional | `get_insider_activity` | public | Insider (Form-4-derived) transaction rows for one non-TW symbol. |
| GET | `/api/v1/funds/{symbol}/holdings` | optional | `get_fund_holdings` | public | Top-holdings + sector-weighting rows for one non-TW ETF/mutual fund. |
| GET | `/api/v1/funds/{symbol}/profile` | optional | `get_fund_profile` | public | AUM/category/issuer/YTD-return/distribution-yield/inception-date for one non-TW ETF/mutual fund — the fund-equivalent of `/stock/company-profile`. |
| GET | `/api/v1/funds/{symbol}/fund-info` | optional | `get_etf_fund_info` | public | Fund-level reference info for one TW listed ETF (issuer, tracked index, units outstanding, listed/inception dates, manager person, custodian bank) —… |
| GET | `/api/v1/funds/{symbol}/expense-ratio` | optional | `get_etf_expense_ratio` | public | Internal-fee (內扣費用) disclosure for one TW fund/ETF: management fee, custody fee, custodian bank, and an approximate TER floor (management + custody). |
| GET | `/api/v1/funds/{symbol}/theme-tags` | optional | `get_etf_theme_tags` | public | TWSE's own 主題/因子 (theme/factor) editorial tags plus 受益人次(萬人) holder count for one TW listed ETF (e.g. |
| GET | `/api/v1/funds/{symbol}/trailing-returns` | optional | `get_etf_trailing_returns` | public | One listed ETF's price return over 6 fixed trailing horizons (1個月/1季/半年/1年/3年/5年), computed from a single split/dividend-adjusted price-history fetch. |
| GET | `/api/v1/funds/{symbol}/premium-discount` | optional | `get_etf_premium_discount` | public | 折溢價 (premium/discount to NAV) history + latest AUM for one TW ETF, over a bounded trailing trading-day window. |
| GET | `/api/v1/funds/overlap` | optional | `get_etf_overlap` | public | Constituent-overlap comparison across 3+ non-TW ETFs/funds — the explicit fix for 'existing tools only compare 2 funds at a time'. |
| GET | `/api/v1/funds/comparison` | optional | `get_fund_comparison` | public | Total-return-with-dividends-reinvested comparison across 1+ symbols in any market (including TW) — a single symbol is a legitimate call too (e.g. |

#### Agent turns

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| POST | `/api/v1/agent/query` | user | — | — | Agent query |

#### Threads

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| POST | `/api/v1/threads` | user | — | — | Create thread |
| GET | `/api/v1/threads` | user | — | — | List threads |
| GET | `/api/v1/threads/{thread_id}` | user | — | — | Get thread |
| DELETE | `/api/v1/threads/{thread_id}` | user | — | — | Delete thread |

#### Thread & turn actions

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| PATCH | `/api/v1/threads/{thread_id}` | user | — | — | Rename / star / archive / tag. |
| POST | `/api/v1/threads/{thread_id}/fork` | user | — | — | Fork from a turn into a fresh thread (shares no message rows with the source). |
| GET | `/api/v1/threads/search` | user | — | — | Full-text-ish search over the caller's own thread titles + message text. |
| GET | `/api/v1/threads/{thread_id}/export` | user | — | — | Export a thread + its history as JSON or Markdown. |
| GET | `/api/v1/threads/{thread_id}/stream` | user | — | — | Attach to a thread's running turn and stream its remaining frames, with durable `Last-Event-ID` resume when the transport backend supports it. |
| POST | `/api/v1/threads/{thread_id}/steer` | user | — | — | Queue a steering message into the running turn (redirect, no cancel). |
| POST | `/api/v1/threads/{thread_id}/switch-branch` | user | — | — | Switch the active path to another message (an O(1) leaf-pointer flip). |
| POST | `/api/v1/threads/{thread_id}/turns/{turn_id}/cancel` | user | — | — | Cancel a running turn (stops its SSE with a cancelled frame). |
| POST | `/api/v1/threads/{thread_id}/turns/{turn_id}/feedback` | user | — | — | Record 👍/👎 + an optional short comment on a turn — a real, persisted write. |
| POST | `/api/v1/threads/{thread_id}/turns/{turn_id}/regenerate` | user | — | — | Re-run the same user input that produced `turn_id`'s assistant answer, producing a NEW sibling assistant turn — a version, not an edit of history. |
| POST | `/api/v1/threads/{thread_id}/turns/{turn_id}/edit` | user | — | — | Edit a user message + resend — creates a NEW branch: a new user turn as a sibling of the original (same parent message), with a new assistant turn as… |

#### Voice

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| POST | `/api/v1/voice/transcribe` | user | — | — | STT → transcript + confidence. |
| POST | `/api/v1/voice/speak` | user | — | — | TTS read-aloud (Free, fair-use bounded like STT: a per-minute rate and a daily cap). |

#### Memory & watchlists

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| GET | `/api/v1/memory` | user | — | — | The caller's memories, most recently changed first; `q` keeps the ones whose key or text contains it. |
| GET | `/api/v1/memory/settings` | user | — | — | Get memory settings |
| PUT | `/api/v1/memory/settings` | user | — | — | Pause or resume long-term memory. |
| DELETE | `/api/v1/memory` | user | — | — | Forget everything (or one scope) in one go. |
| PATCH | `/api/v1/memory/{memory_id}` | user | — | — | Edit a memory's content in place (its key and type stay). |
| POST | `/api/v1/memory` | user | — | — | Upsert memory |
| DELETE | `/api/v1/memory/{memory_id}` | user | — | — | Delete memory |
| GET | `/api/v1/watchlists` | user | — | — | List watchlists |
| POST | `/api/v1/watchlists` | user | — | — | Create watchlist |
| PATCH | `/api/v1/watchlists/{watchlist_id}` | user | — | — | Rename, recolour and/or reorder a group. |
| DELETE | `/api/v1/watchlists/{watchlist_id}` | user | — | — | Delete watchlist |
| POST | `/api/v1/watchlists/reorder` | user | — | — | Reorder watchlists |
| GET | `/api/v1/watchlists/{watchlist_id}/items` | user | — | — | List watchlist items |
| PUT | `/api/v1/watchlists/{watchlist_id}/items/{symbol}` | user | — | — | Star toggle — add. |
| DELETE | `/api/v1/watchlists/{watchlist_id}/items/{symbol}` | user | — | — | Star toggle — remove. |
| PATCH | `/api/v1/watchlists/{watchlist_id}/items/{item_id}` | user | — | — | Patch watchlist item |
| POST | `/api/v1/watchlists/{watchlist_id}/items/{item_id}/move` | user | — | — | Move watchlist item |

#### Pets

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| GET | `/api/v1/pets/catalog` | none | — | — | The full code-defined catalog (rarity, bilingual names, descriptions) — no auth needed to browse, but kept behind the same router group for locality. |
| GET | `/api/v1/pets` | user | — | — | My pets |
| GET | `/api/v1/pets/active` | user | — | — | Active pet |
| PUT | `/api/v1/pets/{record_id}/equip` | user | — | — | Equip |
| GET | `/api/v1/pets/progress` | user | — | — | Progress |
| GET | `/api/v1/pets/settings` | user | — | — | Get settings |
| PATCH | `/api/v1/pets/settings` | user | — | — | Update settings |
| POST | `/api/v1/pets/pull` | user | — | — | Gacha pull (reserve-before-act on `available_pulls` in the store). |

#### Auth & OAuth

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/oauth/{provider}` | none | — | — | Begin sign-in: generate CSRF `state` (+ PKCE/nonce for LINE), stash them in httpOnly cookies, and return the provider authorize URL for the client to… |
| POST | `/api/v1/auth/oauth/{provider}/link` | user | — | — | Begin linking a provider to the already-signed-in user — same as begin, but the callback will attach the verified identity to the current account ins… |
| GET | `/api/v1/auth/oauth/{provider}/start` | none | — | — | Second half of a link started from another origin: redeem the ticket, set the flow cookies on *this* origin (the one the provider will redirect back… |
| GET | `/api/v1/auth/oauth/{provider}/callback` | none | — | — | OAuth redirect target: verify `state`, exchange the code, verify the ID token, resolve the identity (login/signup or link), issue the session cookies… |
| POST | `/api/v1/auth/refresh` | none | — | — | Rotate the refresh token and mint a fresh access token. |
| POST | `/api/v1/auth/logout` | none | — | — | Revoke the current refresh session and clear the auth cookies. |
| GET | `/api/v1/me` | user | — | — | Me |
| DELETE | `/api/v1/me` | user | — | — | Permanently delete the caller's account and every row stored for it (GDPR/PDPA erasure). |
| GET | `/api/v1/me/connections` | user | — | — | Connections |
| DELETE | `/api/v1/me/connections/{provider}` | user | — | — | Unlink a provider — refused if it is the account's only remaining login method. |

#### Account, BYOK keys & usage

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| PATCH | `/api/v1/me` | user | — | — | Update me |
| GET | `/api/v1/personality/presets` | none | — | — | The agent-voice presets for the Settings picker (id, names, description). |
| GET | `/api/v1/usage/quota` | user | — | — | The caller's resolved limits (chats/day, req/min, FinMind class) given tier + BYOK. |
| GET | `/api/v1/usage/today` | user | — | — | What the caller has claimed of today's (UTC) allowances - the consumption half of the limits `/usage/quota` reports. |
| GET | `/api/v1/usage/llm` | user | — | — | The caller's own LLM consumption over the last `days` days, from each provider's per-response usage metadata (daily series, per-model totals; cost on… |
| GET | `/api/v1/me/keys` | user | — | — | Which providers the caller has saved an LLM key for, each with a masked preview - never the keys. |
| POST | `/api/v1/me/keys` | user | — | — | Store an encrypted BYOK key. |
| DELETE | `/api/v1/me/keys/{provider}` | user | — | — | Remove a BYOK key. |

#### Billing (Paddle)

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| GET | `/api/v1/billing/plans` | none | — | — | The tier/interval/byok to price_id catalog for the pricing page, plus the publishable Paddle.js client token. |
| POST | `/api/v1/billing/checkout` | user | — | — | Create a Paddle checkout transaction for the selected plan, tagged with the user id so the webhook can attribute the resulting subscription back to t… |
| GET | `/api/v1/billing/subscription` | user | — | — | The caller's current subscription, reconciled against the live Paddle API so a dropped or delayed webhook self-heals on this read. |
| POST | `/api/v1/billing/sync` | user | — | — | Recover a checkout whose webhook never arrived: look the transaction up at Paddle and adopt the subscription it created, provided the transaction was… |
| POST | `/api/v1/billing/change-plan` | user | — | — | Move the caller's live subscription to another tier / interval / BYOK variant. |
| POST | `/api/v1/billing/resume` | user | — | — | Undo a scheduled cancellation, or restart a paused subscription. |
| GET | `/api/v1/billing/invoice/{transaction_id}` | user | — | — | A short-lived link to Paddle's tax-compliant invoice PDF. |
| POST | `/api/v1/billing/portal` | user | — | — | A link into Paddle's customer portal. |
| POST | `/api/v1/billing/webhook/paddle` | none | — | — | Process a Paddle webhook — the entitlement source of truth. |
| GET | `/api/v1/billing/history` | user | — | — | Proxy Paddle's transactions API for the caller's subscription — Paddle owns the tax- compliant invoices/PDFs, so nothing is mirrored locally. |

#### Alerts, schedules, notifications & channels

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| POST | `/api/v1/schedules` | user | — | — | Create schedule |
| GET | `/api/v1/schedules` | user | — | — | List schedules |
| PATCH | `/api/v1/schedules/{job_id}` | user | — | — | Update schedule |
| DELETE | `/api/v1/schedules/{job_id}` | user | — | — | Delete schedule |
| POST | `/api/v1/alerts` | user | — | — | Create alert |
| POST | `/api/v1/alerts/batch` | user | — | — | All-or-nothing: every item is validated and the fair-use cap is checked for the whole batch before anything is written, and the request runs in one t… |
| GET | `/api/v1/alerts/symbol-check` | user | `get_price_quote` | public | Does this ticker have any price data? A pre-flight the alert form calls so a typo (`APPLE` for `AAPL`) is flagged *before* a rule is saved that could… |
| GET | `/api/v1/alerts` | user | — | — | List alerts |
| PATCH | `/api/v1/alerts/{alert_id}` | user | — | — | Update alert |
| DELETE | `/api/v1/alerts/{alert_id}` | user | — | — | Delete alert |
| GET | `/api/v1/notifications` | user | — | — | List notifications |
| POST | `/api/v1/notifications/read` | user | — | — | Mark notifications read — `{ids: [...]}` for specific ones, `{all: true}` for everything. |
| GET | `/api/v1/notifications/badge` | user | — | — | Notifications badge |
| GET | `/api/v1/me/alert-preferences` | user | — | — | The user's current defaults; a user who never saved any gets the empty defaults (in-app only, no quiet hours) rather than a 404, so the settings form… |
| PUT | `/api/v1/me/alert-preferences` | user | — | — | Put alert preferences |
| GET | `/api/v1/me/channels` | user | — | — | Get channel status |
| POST | `/api/v1/me/channels/telegram/link` | user | — | — | Issue a one-time link: the user opens it, taps Start in the bot, and the webhook below binds that chat to their account. |
| DELETE | `/api/v1/me/channels/telegram` | user | — | — | Unlink telegram |
| POST | `/api/v1/me/channels/line/link` | user | — | — | Issue a one-time code. |
| DELETE | `/api/v1/me/channels/line` | user | — | — | Unlink line |
| GET | `/api/v1/email/unsubscribe` | none | — | — | A confirmation page, not the action: mail scanners open links, so a GET must not unsubscribe anyone. |
| POST | `/api/v1/email/unsubscribe` | none | — | — | The one-click unsubscribe (RFC 8058: mail clients POST `List-Unsubscribe=One-Click` here) and the confirmation button's target. |
| POST | `/api/v1/email/resubscribe` | none | — | — | Undo an unsubscribe with the same signed link. |
| POST | `/api/v1/webhooks/resend` | none | — | — | Resend reports what happened to each email. |
| POST | `/api/v1/channels/line/webhook` | none | — | — | LINE calls this for every event on the official account (the URL is registered in the LINE console). |
| POST | `/api/v1/webhooks/telegram` | none | — | — | Telegram calls this for every message to the bot. |
| POST | `/api/v1/me/devices` | user | — | — | Register device |
| DELETE | `/api/v1/me/devices/{token:path}` | user | — | — | Unregister device |

#### Ops probes (app root, not under /api/v1)

| Method | Path | Auth | Tool | Tier | Summary |
|---|---|---|---|---|---|
| GET | `/livez` | none | — | — | Livez |
| GET | `/readyz` | none | — | — | Readyz |
| GET | `/startupz` | none | — | — | Startupz |

<!-- ROUTES:END -->

---

## Cross-references
- Schema behind these endpoints: [database-schema.md](database-schema.md)
- Rules enforced on every endpoint: [governance.md](governance.md)
- System overview: [architecture.md](architecture.md)
