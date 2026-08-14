# Web — Developer Guide (build-from-blueprint)

The **on-ramp** between the design spec and the code. The spec ([README.md](README.md),
[design-system.md](design-system.md), [interaction-spec.md](interaction-spec.md),
[data-freshness.md](data-freshness.md), and the page docs) is the **reference — what & why**;
this doc is the **runbook — how**: how to set up, in what order to build, how the backend
*actually* answers today, what to stub, and the gate a change must pass.

> **Status: blueprint — no web code exists yet.** `web/` is not scaffolded. This guide is the
> intended setup/toolchain, kept honest against the **real backend** (`backend/src/`, audited from
> code, not from the backend's own docs). The backend design docs describe more surface than is
> built; §4 below is the **what's-real ledger** every page must build against. When a page doc and
> the backend code disagree, **the code wins** — degrade the UI, don't invent an endpoint.

**Before touching anything:** read [README.md](README.md) (IA + nav + stack), [design-system.md](design-system.md)
(tokens + components — the non-negotiables live here), [interaction-spec.md](interaction-spec.md)
(motion/state/a11y contracts), and [data-freshness.md](data-freshness.md) (the cache-coherence
invariant). The **governance UI rules** (§7 here) are load-bearing — never relaxed to make a screen ship.

---

## 1. Quickstart

Toolchain (2026 baseline, from [README §1](README.md#1-stack--foundations)): **Next.js (App Router,
RSC)**, **TypeScript**, **shadcn/ui + Tailwind v4** (own-the-code), **TanStack Table/Query**,
**Zustand** (thin live store), an **SSE client**, a lightweight candlestick lib
(lightweight-charts / visx), **Phosphor** icons.

```bash
# from repo root — once web/ is scaffolded
cd web
pnpm install
cp .env.local.example .env.local     # point NEXT_PUBLIC_API_BASE at the backend (§2)

pnpm dev                              # Next dev server → http://localhost:3000
```

You need the **backend running** for anything data-bearing — see
[../backend/DEVELOPMENT.md §1](../backend/DEVELOPMENT.md). The web app is a pure client of the
`/api/v1` surface; it has no datastore of its own. Fastest loop: backend on SQLite + `uv run
uvicorn`, web on `pnpm dev`.

Smoke-test the wiring against a public read (no auth):

```bash
curl 'http://localhost:8000/api/v1/quote?symbols=2330&market=TW'          # EOD quote (public)
curl 'http://localhost:8000/api/v1/analytics/2330/score?market=TW'        # 多空 score (public)
```

An AI turn is **SSE** (`POST /api/v1/agent/query`) — the answer arrives as one final `message`
frame, not a token stream (§4b). Build the chat client around lifecycle events, not deltas.

---

## 2. Backend contract & environment

The single source of truth for the HTTP/SSE surface is the backend routers under
`backend/src/api/routers/` (the backend's own `api-reference.md` documents more than is built —
trust the routers). Conventions the client must honor:

| Concern | Reality (from code) |
|---|---|
| **Base prefix** | `/api/v1` for all app routers; `/livez`/`/readyz`/`/healthz` at root |
| **Auth** | httpOnly `access_token` cookie (JWT, 15 min) + `refresh_token` cookie (30 d, rotated). `POST /auth/refresh` on 401. OAuth: **Google + LINE only** |
| **Error envelope** | `{code, message, retryable}` (422 = `{code:"invalid_request"}`; 500 = `retryable:true`) — **not** a bare string |
| **Sponsor masking** | sponsor data returns **HTTP 200** with a `MaskedTeaser {required_tier, preview, tool_name}` payload — **never 401/403**. The UI reads the mask and paints 馬賽克 (§4c). There is no `locked` field |
| **Idempotency** | `POST /agent/query` and turn actions take an `Idempotency-Key` header |
| **Optimistic CAS** | `PATCH /threads/{id}` and `PATCH /watchlists/{id}` take `If-Match: <version>` → **409** on stale |
| **Freshness** | quote-stream frames carry `as_of` + `market_open`. **No ETag/`If-None-Match`/304 is implemented** — the freshness contract in [data-freshness.md](data-freshness.md) §3 is a *target*; today anchor revalidation to the known daily/weekly batch boundary only |

Env: `NEXT_PUBLIC_API_BASE` (the backend origin). No secrets live in the web app — LLM/data keys
and OAuth creds are the backend's (`backend/.env`, never read/echoed). Billing amounts come from
**Paddle.js on the client** (the backend does not store amounts); everything `/billing/*` is
config-gated and 503s until Paddle env is set — build the UI, expect it inert until then.

---

## 3. Build order (turn the blueprint into code)

Build the shell first, then the pages that stand on the **most-real** backend, then the pages that
need the biggest stubs. This ordering front-loads working data and defers design-only surfaces.

| Phase | Build | Spec | Backend reality it stands on |
|---|---|---|---|
| 1 | **App shell** — three-column layout, collapsible rail, glass top bar, theme tokens, dark-first, the required `<Caveat>`/馬賽克/`DataFreshnessBadge` primitives | [README §2/§9](README.md), [design-system.md](design-system.md) | none (pure UI) — but the primitives are used everywhere, so they go in first |
| 2 | **Auth + `/me`** — OAuth begin/callback (Google/LINE), session refresh, profile, entitlement envelope (`tier`, `features[]`) | [settings/README.md](settings/README.md) | ✅ real: `/auth/*`, `/me`, `/me/connections`, `/personality/presets` |
| 3 | **Watchlist rail** — groups, drag-reorder (lexorank), idempotent star, preset seeding, `If-Match` reconcile | [watchlist/README.md](watchlist/README.md) | ✅ real: `/watchlists/*`, `/memory`; quotes via `/quote` (EOD) / `/quote/stream` (Premium) |
| 4 | **Market dashboard** — the default landing (`/` → `/market`) | [market.md](market.md) | ⚠️ partial: real `/market/institutional-ranking\|margin-maintenance\|day-trading\|etf-money-flow\|industry-flow`, `/derivatives/{contract}`, `/macro`. **Missing: `/market/overview\|advance-decline\|index-contribution\|sectors`** — stub or degrade (§4a) |
| 5 | **Stock Detail** — the multi-panel workbench | [stock-detail.md](stock-detail.md) | ⚠️ mixed: 財務/籌碼/基本 backed by real tools via chat/agent; **score gauge real** (`/analytics/{s}/score`); **技術分析 has no REST indicators route**, **智能摘要 SWOT unbuilt** (§4a) |
| 6 | **Chat** — SSE companion | [chat/README.md](chat/README.md) | ⚠️ real turn stream, but **one final `message` frame, no token deltas**; `rich_card` + final `caveat` block real; `activity`/`thinking.delta`/`tool.locked`/`follow_up` **not emitted** (§4b) |
| 7 | **Screener** — funnel workbench | [screener/README.md](screener/README.md) | ⛔ biggest gap: the numeric Stage-A/B/C funnel + saved screens + `/analytics/screener/*` **do not exist**. Real today: `/analytics/rankings` (top-N) + the tag-tile screener (agent tool only, no REST) + US Yahoo field screener (§4a) |
| 8 | **Settings** — profile / 語氣 / pets / billing / alerts & schedules / usage / memory | [settings/README.md](settings/README.md) | ✅ mostly real; **`/usage/summary` (live consumption) unbuilt** — only limits via `/usage/quota`; external delivery channels are no-network stubs (§4d) |

**The vertical-slice alternative:** ship phases 1→2→3→4(每日焦點 only, degrading the 4 missing
market routes)→5(基本資料 + score gauge)→6, deferring Screener and the unbuilt analytics until the
backend lands them. Either way the **governance primitives (§7) are not deferrable** — they go in
from phase 1.

---

## 4. What's real, what's stubbed (the ledger every page builds against)

Audited from `backend/src/` — this is the load-bearing section. The page docs already flag most of
this inline; this is the consolidated list so a builder never wires a dead endpoint.

### 4a. REST endpoints

**Real:** `/agent/query`(SSE), `/threads/*` (CRUD + fork/search/export/steer/switch-branch/cancel/
regenerate/edit/feedback), `/quote`, `/health`, `/sources`, `/analytics/{symbol}/score`,
`/analytics/rankings`, `/themes/{key}`, `/themes/{key}/tiers`, `/symbols/{symbol}/themes`,
`/market/institutional-ranking`, `/market/margin-maintenance`, `/market/day-trading`,
`/market/etf-money-flow`, `/market/industry-flow`, `/derivatives/{contract}`, `/macro`,
`/macro/business-indicator`, `/funds/{symbol}/options-chain|institutional-holders|insider-activity|holdings`,
`/funds/overlap`, `/funds/comparison`, `/context/{package}`, `/search` (**US/GB/JP/EU only → 501
for TW**), `/quote/stream`(SSE), `/updates/stream`(SSE), `/watchlists/*`, `/memory`, `/pets/*`,
`/personality/presets`, `/me*`, `/billing/*` (config-gated), `/schedules`, `/alerts`,
`/notifications`, `/me/alert-preferences`, `/me/devices`, `/voice/transcribe|speak`.

**NOT built (page docs reference these — degrade or stub):**
- `/analytics/{symbol}/indicators` — **no `get_indicators` tool exists.** 技術分析 has no REST read
  surface; indicators are computed only internally (score factor + screener batch). Build the K線
  from raw price tools via the agent path, or defer the tab.
- `/analytics/{symbol}/summary` — **AI 智能摘要 / SWOT is unbuilt** (no module, no table, no tool).
  Only the score-gauge half of 智能摘要 is real. Ship the gauge; the SWOT card is design-only.
- `/analytics/screener/factors|run|screens`, `/analytics/themes` — the numeric screener funnel and
  LLM concept-theme endpoints don't exist. See §4a screener note below.
- `/market/overview`, `/market/advance-decline`, `/market/index-contribution`, `/market/sectors` —
  backing tools don't exist. 每日焦點 (大盤概況/breadth/貢獻/treemap) and 熱力圖 have **no REST
  source today** — stub with a coverage-empty state until the tools land.
- `/usage/summary`, `/usage?thread_id=` — no live-consumption endpoint. `/usage/quota` returns
  **limits only** (chats/day, req/min, features), never "X/3 used today".
- `/calendar` — **no calendar REST route.** Calendar data is only the agent tool
  `get_calendar_events` (Sponsor, feed-only: 除權息 + CB events; AGM/法說會/index-rebalance are
  honest gaps). 本週行事曆 / 個股事件 go through the agent path, not a dedicated route.
- `/feedback/quality|alerts|recent` — admin pipeline unbuilt. Only per-turn up/down capture exists.

**Screener reality:** the real screening surface is (1) `/analytics/rankings` (descriptive top-N
leaderboard from the nightly score batch), (2) the **tag-tile screener** — `run_tag_screener` /
`get_tag_catalog` agent tools over 29 tags in 4 categories (技術面/籌碼面/熱門/智能), **no REST
projection yet**, and (3) `run_equity_screener_us` (non-TW Yahoo field query, agent tool). The
numeric AND/OR condition builder + saved screens the [screener](screener/README.md) doc specs is
**not built**. Build the funnel UI against the tag screener + rankings, degrade the custom builder.

### 4b. Chat streaming

The agent loop calls the **blocking** `chat()`, never `stream()` — **there is no token streaming.**
The final answer arrives as **one `message` frame** just before `turn.end`. Real SSE event kinds
(canonical envelope `{seq, ts, turn_id, agent_id, parent_agent_id, node_id, data}`):

`turn.start` · `route.decided` · `agent.spawned` · `agent.progress` (only ever
`{status:"degraded", phase:"quota"}`) · `agent.done` · `tool.result` (redacted summary, never raw
payload) · `rich_card` · `synthesis.start` · `usage` · `steering.accepted` · `message` (the final
answer) · `turn.end` (`{status: done|cancelled|error}` — errors ride here, there is **no** `error`
event).

**NOT emitted** (the chat doc's target vocabulary): `message.delta`, `thinking.delta`, `activity`,
`tool.proposed`, `tool.started`, `tool.locked`, `caveat` (real only as a persisted message *block*,
never a bus event), `follow_up`, `ask_user`, `pet.*`. Build the "activity narration" / "顯示過程" /
`follow_up` chips as **design-forward, absent-today** — render from the final `message` + the
`rich_card`/`tool.result` frames you do get. `node_id` is always `None`; attribute by `agent_id`.

Two more SSE contracts: `GET /threads/{id}/stream` resumes with `Last-Event-ID` (durable only on
the Redis backend; in-memory backend is live-attach only). `GET /quote/stream` uses a **different
framer** — its `data` is the raw quote dict `{symbol, market, quote, as_of, market_open}`, **not**
the full envelope — a parser expecting the envelope on quote frames will break.

### 4c. Sponsor masking / 馬賽克

Sponsor data (分點/官股/主動ETF holdings/`price_intraday`/市值/產業鏈資金流/CB/月營收-batch) comes
back at **HTTP 200** as `MaskedTeaser {required_tier, preview, tool_name}` for unentitled users. The
UI paints 馬賽克 from that payload — frame/labels sharp, values blurred. Entitlement is resolved
**per call**, so a tier upgrade unmasks on the next fetch (bust the cached masked cell —
[data-freshness §7](data-freshness.md) #3). Tiers: **Free / Premium / Pro**; chat caps **3 / 100 /
∞**; BYOK lifts the chat cap only.

### 4d. Other reality notes

- **User-facing markets are TW + US only.** `watchlists`, `alerts`, and `stock_scores` hard-CHECK
  `market IN ('TW','US')` even though the source layer serves GB/JP/EU/GLOBAL. Market tabs beyond
  TW/US/ETF are not wireable today.
- **ETF 折溢價 (premium/discount) & TW per-constituent holdings/NAV are unavailable** — ETF PCF is
  fund-level metadata only; `premium_discount_pct` is always `None`; `get_etf_overlap` rejects TW.
  The 折溢價 chart + 被N支ETF持有 reverse view have no TW data source. `/funds/overlap` &
  `/funds/comparison` are the real ETF-analysis surface (US-focused).
- **External delivery channels are no-network stubs** — only `in_app` notifications truly deliver.
  LINE/Telegram/email/push shape the request but don't send. `telegram` exists in code but isn't in
  the settings channel-picker doc. Present external delivery as best-effort/not-yet-live.
- **Score = 5 factors** (momentum/trend_quality/value_guard/quality/revenue_flow), band 偏多/中性/偏空,
  cache-only (nightly batch — `found:false` with a message if the batch hasn't run), regime is a
  fixed caveat not a live read. Not 15 factors, no analyst overlay, no news-sentiment sleeve.
- **Personality presets** (real, 4): `steady`(穩健, default) / `concise`(簡潔) / `data`(數據派) /
  `coach`(白話). Set per-user (`PATCH /me {personality_id}`) or per-thread override; **per-turn pin
  has no API**. Free-text persona is deliberately impossible (injection guard).
- **News/圖表/社群:** the only per-stock news tool is `get_finmind_stock_news` (agent path). There is
  **no** `get_stock_news`/`get_social_buzz`/`get_figure_events` — 即時/財經/PTT/名人 news surfaces
  have no dedicated backend today; `web_search` is the general fallback.

---

## 5. Testing runbook

- **Component/unit:** Vitest + Testing Library. Assert the **non-negotiables** (§7) as tests:
  紅漲綠跌 pairs a ▲▼ + sign (never color-alone), prices use `tabular-nums`, the `<Caveat>` +
  non-trading footer render on every analytical surface, sponsor payloads paint 馬賽克.
- **Contract:** mock the `/api/v1` surface from the **real router shapes** (§4), including the
  `{code, message, retryable}` error envelope, the `MaskedTeaser` 200, and the SSE event set in §4b.
  A fixture that emits `message.delta` is wrong — the backend never sends it.
- **E2E:** Playwright ([Test Automation Engineer] conventions) — resilient selectors, the SSE turn
  (assert the single `message` frame + `turn.end`), `Last-Event-ID` resume, optimistic
  watchlist reorder → `version` 409 snap-back.
- **a11y:** axe + keyboard walk of the workbench (screener table, chart controls, thread list);
  `prefers-reduced-motion` collapses gacha/gauge/marquee to static; WCAG AA both themes.

**Build-time review (deferred):** when a screen actually ships, run the **`impeccable`** agents
against the *built* artifact ([README §1](README.md#build-time-review-deferred--when-the-ui-is-implemented-not-now)) —
not the spec docs. Nothing to review at blueprint stage.

---

## 6. "How to add X" recipes

**Add a page/route** → the owning page doc + [README §2](README.md)
1. RSC route under `app/`; server-fetch cold data (EOD panels, tables) with `revalidate` anchored to
   the class boundary ([data-freshness §5](data-freshness.md)). Client components only for
   interactive/live bits.
2. **Check §4 first** — if the endpoint isn't real, build the coverage-empty/degraded state, not a
   dead fetch. Add a `⚑` note in the page doc's backend-mapping table.

**Add a data read** → [data-freshness.md](data-freshness.md)
1. Mirror the backend key `(capability, market, symbol[, date])` as the TanStack `queryKey`.
2. One owner per value (RSC *or* TanStack, never both). Read TTL from the single `FRESHNESS[class]`
   map. Live `quote` ticks go to the **Zustand** live layer, never the query cache.

**Add a chat-stream consumer** → §4b, [chat/README.md](chat/README.md)
1. Subscribe to the SSE kinds that exist (§4b). Render the answer from the final `message` frame +
   `rich_card`/`tool.result`. Do not build on `*.delta`/`activity`/`follow_up` — gate those behind a
   feature flag so they light up if/when the backend emits them.

**Add a component** → [design-system.md](design-system.md)
1. shadcn/ui + Tailwind tokens; never ship a default-state component. Every analytical card composes
   the required `<Caveat>` + `DataFreshnessBadge`. Sponsor-bearing cards compose the 馬賽克 layer.

---

## 7. The governance gate (before every merge)

These mirror [../backend/governance.md](../backend/governance.md) at the UI layer — **non-removable
design-system components**, not optional polish:

- **紅漲綠跌 — red = up, green = down**, and **never color-alone** → always ▲▼ + sign. `--accent`
  teal is chrome-only, never a price.
- **`<Caveat>` on every analytical surface** — non-trading on analysis, currentness on quotes.
  Copy never uses 明牌/飆股/目標價/買賣/進場/停損. Personality/pet layers style tone only — never
  drop a caveat or introduce trading language.
- **Sponsor → 馬賽克** from the `MaskedTeaser` payload; entitlement upgrade busts the masked cell.
- **Honest freshness** — `DataFreshnessBadge` states EOD vs ~5s-delayed; a delayed/EOD value is never
  painted as 即時; source lag degrades to "資料延遲/部分不可用", never silently as fresh.
- **tabular-nums** on all prices/%/scores; **dark-first**; **WCAG AA** both themes;
  **`prefers-reduced-motion`** honored.
- **Analyst/external data attributed** ("N analysts, as of <date>"), never restated as ours.

A change that weakens one of these to ship is a **reject**, not a merge.

---

## 8. Cross-references

- IA / stack / nav: [README.md](README.md)
- Tokens / components: [design-system.md](design-system.md)
- Motion / state / a11y: [interaction-spec.md](interaction-spec.md)
- Cache coherence / freshness: [data-freshness.md](data-freshness.md)
- Page docs: [stock-detail.md](stock-detail.md) · [market.md](market.md) ·
  [screener/README.md](screener/README.md) · [chat/README.md](chat/README.md) ·
  [watchlist/README.md](watchlist/README.md) · [settings/README.md](settings/README.md)
- Backend truth: `backend/src/api/routers/` (the routers, not the backend's api-reference.md) ·
  [../backend/DEVELOPMENT.md](../backend/DEVELOPMENT.md) · [../backend/governance.md](../backend/governance.md)
