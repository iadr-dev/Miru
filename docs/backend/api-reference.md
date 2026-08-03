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
- **Read-model freshness** (cache coherence — [modules/16](modules/16-infrastructure.md) §3a,
  [web data-freshness.md](../web/data-freshness.md)): every cacheable read (`/quote`, `/analytics/*`,
  `/market/*`) returns an **`as_of`** timestamp (value production time) + a strong **`ETag`**;
  `If-None-Match` → **`304`**. `Cache-Control: private, max-age=0, stale-while-revalidate=<class
  boundary>`. A **`data.updated`** SSE event (`{capability, market, as_of, scope}`) fires when a
  derived-compute batch lands so clients invalidate precisely instead of waiting a TTL. The `quote`
  SSE payload carries per-tick `as_of` + `market_open`.

---

## 1. Agent & streaming ([modules/07](modules/07-conversation-streaming.md))

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/agent/query` | Start a turn (thread_id optional → auto-creates); returns SSE. Body flags: `web_search` (per-turn, [adr/0014]), **`research`** (deep multi-step research — **Pro**, [modules/11](modules/11-orchestration.md); teaser if unentitled), **`plan`** (plan-first mode — the classifier drafts a plan before executing), `model`, `skill_hint`, `personality` (one-shot voice preset, [modules/23](modules/23-agent-personality.md)), `Idempotency-Key`. The composer `+` menu maps to these flags |
| GET | `/threads/{id}/stream` | Attach/resume live stream (`Last-Event-ID`) |
| POST | `/threads/{id}/steer` | Queue a **steering** message into the running turn (redirect, no cancel) |
| POST | `/threads/{id}/switch-branch` | Switch active path to another message (generates a Branch Summary) |
| POST | `/threads/{id}/turns/{tid}/cancel` | Cancel a running turn (stop SSE) |

All tools are read-only and run without approval. If a turn calls a `sponsor` (paid FinMind)
tool for an unentitled user, that section streams back as a **masked teaser** (`tool.locked`)
and the answer proceeds around it — no confirmation step. See governance §4.

### SSE event types (every frame carries a correlation envelope: `seq, ts, turn_id, agent_id, parent_agent_id, node_id`)
`turn.start` · `route.decided` · `agent.spawned` · `agent.progress` · `agent.done` ·
`steering.accepted` · `activity` (NL narration) · `message.delta` · `thinking.delta` (raw,
collapsed) · `tool.proposed` · `tool.started` · `tool.result` · `tool.locked` · `tool.blocked` ·
`caveat` · `synthesis.start` · `rich_card` · `ask_user` · `follow_up` · `usage` · `turn.end` ·
`error`.
The `agent_id`/`parent_agent_id` envelope lets the client render a **live timeline with
parallel sub-agent lanes** (Claude Code-style); **`activity`** carries human-readable
narration (the primary line), while `thinking.delta`/tool internals are opt-in detail.
Full semantics + run-tree: [modules/07](modules/07-conversation-streaming.md).

---

## 2. Threads / history ([modules/13](modules/13-chat-interaction-features.md))

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/threads` | Create thread |
| GET | `/threads?sort=updated\|created\|starred&filter=archived&q=` | List (paginated, sortable) |
| GET | `/threads/{id}` | Thread + history |
| PATCH | `/threads/{id}` | Rename title, star/pin, archive, tag |
| DELETE | `/threads/{id}` | Soft-delete → purge |
| POST | `/threads/{id}/fork` | Fork from a turn |
| GET | `/threads/search?q=` | Full-text search (title + messages) |
| GET | `/threads/{id}/export?format=md\|json` | Export |

### Message actions
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/threads/{id}/turns/{tid}/feedback` | 👍/👎 + comment |
| POST | `/threads/{id}/turns/{tid}/regenerate` | Re-run same input (optional `model`) |
| POST | `/threads/{id}/turns/{tid}/edit` | Edit user message + resend (branches in-tree via `switch_branch`; old branch → BranchSummaryEntry) |

---

## 3. Voice ([modules/13](modules/13-chat-interaction-features.md))

| Method | Path | Purpose | Tier |
|--------|------|---------|------|
| POST | `/voice/transcribe` | STT: audio (m4a/webm-opus, ≤~60s) → **transcript + confidence**; `language` default `zh-TW`, watchlist/財經 `hints`. **Returns text only — does NOT create a turn** (user confirms/edits, then `POST /agent/query`) | **Free** (quota-bounded; the beginner ask-loop) |
| POST | `/voice/speak` | TTS: `{turn_id\|text}` → audio (zh-TW SSML; spoken text **includes the caveat**) | Premium+ |

Raw audio is discarded after transcription (PII/PDPA); low-confidence/ambiguous symbols →
`ask_user`. STT/TTS are separate config-selected providers (self-host whisper or cloud).

---

## 4. Context & market data (read-only)

| Method | Path | Purpose | Access |
|--------|------|---------|--------|
| GET | `/context/{package}` | Canonical or conversation package (`canonical`, `conversation`) | public |
| GET | `/search?q=&market=` | **Symbol type-ahead** for the top-bar palette / mobile Stock tab — matches 代號/名稱/產業 over the **symbol universe** (ingested `TaiwanStockInfo` + ETF list + registered US symbols, [modules/19](modules/19-data-access-router.md) §symbol-universe); returns `[{symbol, name, market, asset_class, sector}]` grouped TW/ETF/US + optional 主題/產業 matches; prefix-before-substring ranking. Universe is client-cacheable (long TTL) | public |
| GET | `/quote?symbols=&market=` | EOD / last-snapshot price (`price_eod`) — one-shot | public |
| GET | `/quote/stream?symbols=&market=` | **Live-ish quote feed (SSE)** — `quote` events (each with `as_of` + `market_open`) pushed ~5s while market-open via the shared demand-driven poller (governance §9b, [modules/16](modules/16-infrastructure.md) §2a); delayed snapshot, not true streaming | **Premium+ (masked teaser / EOD otherwise)** |
| GET | `/updates/stream` | **Cache-invalidation feed (SSE)** — `data.updated` events `{capability, market, as_of, scope}` when a derived-compute batch lands ([modules/16](modules/16-infrastructure.md) §3a, [modules/22](modules/22-data-ingest.md) §2); lets clients revalidate precisely | public |
| GET | `/sources` | Source adapter matrix / capabilities | public |
| GET | `/health` | Source-health view (currentness + FinMind plan/quota) | public |

Data inventory: [data-catalog.md](data-catalog.md). Responses containing `sponsor` (paid
FinMind) fields are masked (`{locked, required_tier, preview}`) for unentitled users — the
frontend blurs them with an upgrade CTA. No approval endpoint exists.

---

## 4a. Analytics, scoring & screener ([modules/20](modules/20-analytics-scoring.md))

| Method | Path | Purpose | Access |
|--------|------|---------|--------|
| GET | `/analytics/{symbol}/indicators?set=ma,kd,macd,rsi,bollinger,volume,sr,wave` | Indicator + 量能/支撐壓力/N波 values + series | public |
| GET | `/analytics/{symbol}/score?profile=swing\|position` | lean 5-factor 多空 composite + 偏多/中性/偏空 + breakdown + regime caveat | public (分點 flow sub-signal masks if unentitled) |
| GET | `/analytics/{symbol}/summary` | AI 智能摘要 + 市場定位 + SWOT | Premium+ (teaser otherwise) |
| GET | `/analytics/screener/factors?market=TW\|US\|ETF` | Bilingual catalog + **per-market `fields[]`** (unit/min/max/step/enum/preset-chips) + presets | public |
| POST | `/analytics/screener/run` | **Funnel** — gates→setup: `{market, preset\|screen_type\|conditions[], match, trade_date?, gate_overrides?, count_only?}` (custom AND/OR builder; `count_only` = survivor probe) | public/sponsor per criteria |
| GET | `/analytics/rankings?profile=swing&sort=composite&n=20&screen=` | AI top-N (gated→setup→scored) | public (sponsor-criteria masks) |
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

## 4a-2. Market, derivatives & macro (REST over the module-03 tools)

Thin REST (or RSC/BFF) surface over the market/derivatives/macro tools — the dashboard + new
screens need these as HTTP, not just agent tools (closes the gap flagged in [../web/market.md](../web/market.md) §2).

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

All non-trading, caveated; derivatives framed as descriptive positioning (not a directional call).

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
| GET | `/watchlists?market=TW` | My groups for a market tab (ordered), each with `item_count`; omit `market` → all. **Seeds the default presets (`config.default_watchlist_presets`) on register** so the rail is never cold-empty. |
| POST | `/watchlists` | Create group `{name, market}` (appended, lexorank end key) |
| PATCH | `/watchlists/{id}` | Rename `{name}` and/or reorder `{after_id\|before_id}`; `If-Match: <version>` |
| DELETE | `/watchlists/{id}` | Soft-delete group + cascade items |
| POST | `/watchlists/reorder` | Bulk reorder `{ordered_ids[]}` — full-array fallback for a big reshuffle |
| GET | `/watchlists/{id}/items` | Items in a group (usually returned inline by the group GET) |
| PUT | `/watchlists/{id}/items/{symbol}` | **Star toggle — add** (idempotent, `ON CONFLICT DO NOTHING`); body `{note?, after_id?}` |
| DELETE | `/watchlists/{id}/items/{symbol}` | **Star toggle — remove** (idempotent; 204 regardless) |
| PATCH | `/watchlists/{id}/items/{item_id}` | Set/clear `{note}` and/or reorder within group |
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
| POST | `/auth/oauth/{provider}` | Begin OAuth (google/line) — sets `state`+PKCE `code_verifier`, redirects to provider authorize URL |
| GET | `/auth/oauth/{provider}/callback` | OAuth redirect target (`?code&state`) — verify state, exchange code (secret + `code_verifier`), upsert user+oauth_account, issue JWT. LINE auto-login failure → retry w/ `disable_auto_login=true` |
| POST | `/auth/refresh` | Rotate JWT access token via refresh token |
| POST | `/auth/logout` | Revoke session |
| GET | `/me/connections` | Linked providers (google/line) — Settings |
| POST | `/auth/oauth/{provider}/link` | Link a provider to the **current** user (multi-provider = one account) |
| DELETE | `/me/connections/{provider}` | Unlink a provider (refused if last login method) |
| GET | `/usage/quota` | Usage vs limits (chats/day, req/min, FinMind, cost) |
| GET | `/usage?thread_id=` / `/usage/summary` | Usage accounting |
| POST | `/me/keys` | Store BYOK key (encrypted) |
| DELETE | `/me/keys/{provider}` | Remove BYOK key |

---

## 7. Scheduler & alerts ([modules/14](modules/14-scheduler-alerts.md)) — Pro; §9a carve-out

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/schedules` | Create (validated: user-authorized, read-only, quota-checked) |
| GET | `/schedules` | List mine |
| PATCH | `/schedules/{id}` | Enable/disable/edit |
| DELETE | `/schedules/{id}` | Delete |
| POST | `/alerts` | Create alert rule |
| GET | `/alerts` | List mine |
| PATCH/DELETE | `/alerts/{id}` | Edit/delete |
| GET | `/notifications` | Delivered notifications |
| GET | `/notifications/badge` | Lightweight counts `{unread_notifications, unread_threads, pending_alerts}` — mobile tab-island + bell dots (glyph+count, never color-alone) |
| PUT | `/me/alert-preferences` | Channels, quiet hours, digest |
| POST | `/me/devices` | Register native push token `{platform: ios\|android, token}` (idempotent); payload deep-links `{route, symbol?, thread_id?}` ([14](modules/14-scheduler-alerts.md) §4) |
| DELETE | `/me/devices/{token}` | Unregister (logout / rotation) |

---

## 7b. Billing — Paddle ([modules/15](modules/15-payments-paddle.md))

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/billing/plans` | tier×interval×BYOK → price_id + display amount (from Paddle) |
| POST | `/billing/checkout` | Create Paddle checkout `{price_id}` (custom_data.user_id) |
| GET | `/billing/subscription` | Current subscription; **reconciles-on-read** vs Paddle (self-heals a missed webhook) |
| POST | `/billing/portal` | Paddle-hosted management URL (update card / cancel / invoices) |
| GET | `/billing/history` | **Proxies Paddle's transactions API** (no local invoices mirror) |
| POST | `/billing/webhook/paddle` | Paddle events (signature-verified) — **entitlement source of truth** |

BYOK checkbox on the pricing page switches `price_id` to the cheaper BYOK variant. `tier`/
`byok` are set only by the verified webhook, never by the client.

---

## 8. Feedback admin ([modules/13](modules/13-chat-interaction-features.md))

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

### Immediate tools (dedicated)
| Tool | data_class | Input | Output |
|------|-----------|-------|--------|
| `get_canonical_context` | public | `{symbols[], fields?}` | canonical package |
| `get_market_context` | public | `{}` | index/breadth overview |
| `get_source_matrix` | public | `{}` | adapter capabilities |
| `build_conversation_package` | public | `{symbols[], window?}` | conversation package |
| `get_source_health` | public | `{}` | source health + FinMind quota |
| `search_tools` / `execute_tool` | public | `{intent}` / `{id, args}` | long-tail discovery + run |

### Deferred (via `execute_tool` — ~50 tools total exceed the per-turn schema budget)
`get_chip_flows`, `get_government_bank_flows`, `get_realtime_snapshot`, `get_disposition_stocks`,
`get_company_profile`, `get_stock_news`, … — `sponsor` ones return a masked teaser if unentitled.

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
