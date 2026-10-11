# Architecture

## 1. Purpose & framing

This framework turns the reference project's **read-only, governed TW market data
workbench** into an **autonomous-but-bounded analytical agent** — the way Claude Code
turns a shell + filesystem into a coding agent — delivered as a **multi-user hosted chat
product** (web/mobile) with a **read-only FastAPI API + MCP server**.

The parallel to Claude Code is deliberate:

| Claude Code | This framework |
|-------------|----------------|
| Agentic loop over a coding task | Agentic loop over a market-context question |
| Tools: Read, Edit, Bash, Grep… | Tools: canonical context, build context, bounded observation, source health |
| Permission gating on risky tools | Entitlement gating on paid (FinMind Sponsor) data only |
| Context/window management & compaction | Same, over the context pipeline |
| Provider = Claude | Provider-agnostic adapter (Anthropic/OpenAI/Gemini/Groq/Cerebras/OpenRouter/Ollama) |
| Never runs destructive commands silently | Never emits trading signals; live observation is bounded |

The **data/context layer preserves the reference project's governed semantics**
(Canonical / Bounded Observation / Conversation / Source Health, and the multi-source
builder) so behavior and governance stay compatible. The **agent runtime, LLM adapter,
interaction/product layers, and interfaces are new**.

### Governance concept ↔ code mapping

The reference project's module codes are kept as **governance/mode concepts** (see
[governance.md](governance.md)); the code uses clean domain names — no `m5k_*` filenames.

| Concept (Mode / Level) | Code module | Meaning |
|------------------------|-------------|---------|
| **M5F** Canonical (Mode A / L1) | `context/canonical.py` | Immutable preserved context |
| **M5K** Bounded Observation (Mode B / L2) | `context/observation.py` | Explicit live-ish check |
| **M5N** Conversation (Mode C) | `context/conversation.py` | AI-safe caveated artifact |
| **M5Q** Source Health | `context/source_health.py` | Provider currentness diagnostics |
| **M8A/B/C** Builder | `context/builder.py` | Multi-source context builder |
| **M6D** TLS policy | `sources/tls.py` | `strict\|compatibility\|unsafe-explicit` |

## 2. Layered architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Interfaces (read-only)                          │
│   FastAPI  (REST + SSE)          MCP server (FastMCP, read-only)        │
└───────────────────────────────┬────────────────────────────────────────┘
                                 │
┌───────────────────────────────▼────────────────────────────────────────┐
│         Product layer   accounts · entitlements · scheduler · voice      │
├──────────────────────────────────────────────────────────────────────────┤
│         Interaction layer   orchestrator · conversation · memory ·        │
│                             events/hooks · skills · feedback · usage       │
├──────────────────────────────────────────────────────────────────────────┤
│                    Agent runtime  (backend/src/agent)                     │
│         loop · planner · context_window · guardrails                      │
└───────────────┬───────────────────────────────────┬─────────────────────┘
        LLM Adapter (backend/src/llm)        Tool Registry (backend/src/tools)
   ┌────────┬────────┬─────────┐          ┌───────────────────────────────┐
   │ Claude │ OpenAI │ Local   │          │ read-only tools (all runnable)│
   └────────┴────────┴─────────┘          │ data_class: public | sponsor  │
                                          │ sponsor → entitlement teaser  │
                                          └───────────────┬───────────────┘
                                                          │ tools call…
┌──────────────────────────────────────────┐  ┌────────────────────────────┐
│  Context pipeline (backend/src/context)    │  │  Analytics (backend/src/    │
│  + calendar                                │  │  analytics) — derived compute│
│  canonical·observation·conversation·       │  │  indicators · 多空 score ·   │
│  source_health·builder                     │  │  AI summary/SWOT · screener  │
└────────────────────┬───────────────────────┘  └──────────────┬─────────────┘
        │ request a CAPABILITY for a (symbol, market)           │ read derived store (read-only Parquet)
        │  — never name a source                                │
┌───────▼────────────────────────────────────────────┐        │
│         Data-access router (backend/src/dataaccess)  │        │
│   capabilities · Market/Symbol · (market,capability)→│        │
│   ordered adapters + priority-fallback + coverage-   │        │
│   gate + normalized output (config-driven)           │        │
└───────┬──────────────────────────────────────────────┘        │
        │ router dispatches…                                     │
┌───────▼────────────────────────────────────┐   ┌──────────────▼─────────────┐
│        Source adapters (backend/src/sources)│──▶│  Ingest (backend/src/ingest)│
│  ALL network I/O · TLS · currentness caveats│   │  leader-only writer; cron/  │
│  twse/tpex/taifex_openapi · *_mis ·         │   │  backfill (§9c) → writes ─┐ │
│  ncdr·finmind·mops·news·ptt·etf_pcf·         │   └───────────────────────────│─┘
│  web_search·yfinance                          │                    ┌──────────▼─────────┐
└──────────────────────────────────────────────┘                    │ Derived store       │
                                                                     │ DuckDB/Parquet+Redis│
                                            analytics + context read◀│ (Hive-partitioned)  │
                                                                     └─────────────────────┘
```
The **ingest** layer is a background writer (leader-elected), not in the request path: it pulls
via the source adapters and writes the **derived store**; the context pipeline and analytics
**read** that store (analytics read-only over Parquet, [modules/16](modules/16-infrastructure.md) §3).

**Dependency rule (one direction only):** interfaces → product → interaction → runtime →
tools → context → **dataaccess** → sources. Nothing lower reaches up; nothing but `sources/`
performs network I/O. Callers name a **capability + (symbol, market)**, never a source — the
router resolves *which* adapters, in per-market priority order with fallback ([modules/19](modules/19-data-access-router.md)).

## 3. Data sources

| Source id | Adapter | Role | Timing class | Market |
|-----------|---------|------|--------------|--------|
| `TWSE_OPENAPI` | `twse_openapi.py` | Official latest EOD | official-eod | Listed |
| `TPEX_OPENAPI` | `tpex_openapi.py` | Official OTC + emerging EOD | official-eod | OTC / 興櫃 |
| `TAIFEX_OPENAPI` | `taifex_openapi.py` | Derivatives EOD / reference | official-eod | Derivatives |
| `TWSE_MIS` | `twse_mis.py` | Browser-observed snapshot | live-ish (bounded) | Listed / OTC |
| `NCDR_DGPA_CLOSURE_CAP` | `ncdr_closure.py` | Work-suspension / closure evidence | event-driven | Taiwan-wide |
| `FINMIND` | `finmind/` (package: 9 category modules) | 104 datasets across Technical/Chip/Fundamental/Derivative/Real-Time/CB/Others/International/Global-Economic; gap-filler | mixed | All |
| `MOPS` | `mops.py` | 公開資訊觀測站 disclosure stream via TWSE's own OpenAPI (`t187ap04_L`/`t187ap11_L`) — real JSON, not the dead legacy HTML scraper — 重大訊息/董監事持股 | event-driven / reference | All |
| `NEWS` | `news.py` | Financial RSS (鉅亨/經濟日報/TechNews) + GDELT (keyless) + Finnhub | reference (near-RT) | All |
| `PTT` | `ptt.py` | PTT boards (**scraped**): Stock/Tech_Job buzz + Beauty/Gossiping off-topic | reference (near-RT) | All |
| `ETF_PCF` | `etf_pcf.py` | Passive ETF 成分股/權重 + 受益人數 + NAV/units (**scraped**: issuer PCF/公會/TWSE `fund/`) | official-eod | All |
| `WEB` | `web_search.py` | **Brave Search API** web search + fetch (provider-agnostic); LLM-native search when available | reference (live) | All (toggle) |
| `YFINANCE` | `yfinance/` (package: domain-organized modules) | TW gap-filler (analyst consensus/目標價/ESG) **and** full primary source for non-TW markets (US/GB/JP/EU) — price/financials/holders/discovery/screener; attributed 3rd-party | reference | All |
| `TDCC` | `tdcc/` (package: `_adapter.py`/`_registry.py`/`_shared.py`/`raw_query.py`) | 集保結算所 (TDCC) OpenData — free, keyless CSV dumps: securities info, pledged custody shares, shareholding distribution, offshore fund NAV, e-voting schedule, monthly custody accounts | official-eod | TW-only |

**Critical caveat, carried on every result:** no source is real-time. `*_MIS` are
bounded snapshots, not streams. OpenAPIs are latest-only with no backfill (FinMind adds
history). Every payload is tagged with source id, timing class, fetch timestamp, and a
currentness caveat that propagates to the agent's output. Full inventory:
[data-catalog.md](data-catalog.md). Note: `TAIFEX_MIS` and a standalone `CALENDAR_EXT` adapter
named in earlier drafts of this doc were never built as separate source files — TAIFEX
derivatives snapshots are staged behind `context/builder.py`'s validation flag (§4), and
calendar-gap feeds are composed directly in `calendar/` rather than via a dedicated adapter.

## 4. Context pipeline

- **Canonical (Mode A / Level 1)** — `context/canonical.py`. Immutable, preserved market
  context; built and validated once, never mutated.
- **Bounded Observation (Mode B / Level 2)** — `context/observation.py`. A single explicit,
  operator-triggered live-ish check. No polling.
- **Conversation (Mode C)** — `context/conversation.py`. The AI-safe artifact: caveated,
  no raw payloads, non-trading. **What the agent reasons over** and hands to any external chat.
- **Source Health** — `context/source_health.py`. Provider currentness/closure diagnostics.
- **Builder (M8A/B/C)** — `context/builder.py`. Multi-source merge: official EOD, TAIFEX
  derivatives, and TAIFEX_MIS staged behind a validation flag.

## 5. Tool registry — the agent's action surface

Every action goes through a **tool**; the agent never touches network/filesystem directly.
All tools are **read-only and always runnable** — no safety tiers, no approval gate. Each
tool declares `name`, Pydantic input/output models, a `data_class`, and `run()`.

| `data_class` | Meaning | Example tools | Access |
|--------------|---------|---------------|--------|
| `public` | Gov OpenAPI + FinMind Free/Backer + derived | `get_canonical_context`, `build_conversation_package`, `get_source_health`, `run_bounded_observation` | All users + MCP |
| `sponsor` | Paid FinMind Sponsor data | `get_chip_flows` (分點), `get_government_bank_flows` (八大官股), `get_intraday_kbar`, `get_us_stock_price_minute` | **Premium/Pro only** |

The full registry is **107 real tools** across 24 domain files (chip/technical/fundamental/
derivatives/analyst/international/macro/others/theme/score/screener/tdcc/ask_user, etc.) — this
table shows a representative slice; the authoritative list is
[modules/03-tools.md](modules/03-tools.md).

The single access gate: a `sponsor` tool called by an unentitled user returns a **masked
teaser** (`{locked, required_tier, preview}`) → frontend blurs it (馬賽克) with an upgrade
CTA. See [modules/03-tools.md](modules/03-tools.md).

## 6. Agent runtime (`backend/src/agent/`)

- **`loop.py`** — the Step loop (Pi-style two-stage build): `step_start → transform_context →
  convert_to_llm → llm.chat → hooks(pre_tool/dispatch/post_tool) → hooks(pre_output) → step_end`,
  repeating until a final caveated answer. A **Turn** = one user request→response; a **Step** =
  one LLM round + its tool calls ([modules/05](modules/05-agent-runtime.md)).
- **`planner.py`** — a thin, optional pre-pass (for `single`) that seeds a tool plan + step budget;
  defers to a skill body if one prescribes the tool order.
- **`context_window.py`** — token budgeting + compaction (`contextTokens > window − reserve`),
  splitting only at Assistant boundaries, **preserving canonical facts and caveats**.
- **`guardrails.py`** — mandatory `pre_tool`/`pre_output` hook: blocks trading language + full-
  market scans, verifies caveats, attributes externals. The `sponsor` entitlement check is a
  *separate* mandatory hook (`accounts/entitlements`) — both run via [modules/10](modules/10-events-hooks.md).

### 6a. The agent is assembled from seven modules (map)

The runtime kernel is small; the "agent" is these layers composed around it — each is its own spec:

| Concern | Module | Role in a turn |
|---------|--------|----------------|
| **Orchestration** | [11](modules/11-orchestration.md) | classifies the query → `none/single/subagent/team` + 0–4 skills + model routing |
| **Runtime loop** | [05](modules/05-agent-runtime.md) | the Step loop, planner, compaction, guardrails, journal |
| **LLM adapter** | [04](modules/04-llm-adapter.md) | provider-agnostic `chat(messages, tools)`, streaming, role-tiered |
| **Skills** | [08](modules/08-skills.md) | the selected playbook's tool-call procedure + boundaries (3-level load) |
| **Memory** | [09](modules/09-memory.md) | working/session/long-term recall injected in `transform_context` |
| **Events/hooks** | [10](modules/10-events-hooks.md) | the enforcement seam (guardrails + entitlement) + usage accounting; SSE is a projection |
| **Conversation/streaming** | [07](modules/07-conversation-streaming.md) | thread/message tree, typed SSE, steering, branch-switch; the journal |
| **Personality** | [23](modules/23-agent-personality.md) | the base analyst voice (a prompt layer, §6b) |

### 6b. System-prompt layering (fixed precedence)

`transform_context` assembles the system prompt in one immovable order — lower layers may only
*style*, never override, higher ones — all subject to the `pre_output` guardrail backstop:

```
[1] Governance prompt   (non-trading, caveats, provenance)   ← immovable
[2] Agent personality   (voice/verbosity, modules/23)
[3] Skill body          (task procedure, modules/08, if a skill is active)
[4] Pet persona hint    (cosmetic warmth, modules/21)
        ↓ every final text passes ↓
   guardrails.check_output  (modules/05 §guardrails)
```

Conversation state and the auditable journal live in `backend/src/conversation/`
([modules/07](modules/07-conversation-streaming.md)).

## 7. LLM adapter (provider-agnostic)

`LLMClient` protocol: `chat(messages, tools) -> text | tool_calls`, streaming, token
counting, tool-call formats normalized across providers. Clients: Anthropic (default),
OpenAI, Gemini, Groq, Cerebras, OpenRouter, Ollama (local). Provider/model chosen from
config/env; role-based cost-tiered routing.
See [modules/04-llm-adapter.md](modules/04-llm-adapter.md) and
[adr/0001-provider-agnostic-llm.md](adr/0001-provider-agnostic-llm.md).

## 8. Anatomy of one agent turn (sequence)

Which module owns each step is noted in `[brackets]`.

```
User → POST /agent/query "today's context on 2330 and 0050"        [07 conversation]
  │  rate-limit / daily-chat gate (pre_turn hook)                   [10 hooks · 12 tiers]
  ▼
Orchestrator classifies → framework + 0–4 skills + model routing   [11 orchestration]
  │  route.decided event journaled
  ▼
runtime builds system prompt via transform_context:                [05 runtime]
  governance → personality → skill body → pet hint (§6b)           [23 · 08 · 21]
  + memory recall + branch summaries + compaction if over budget    [09 memory]
  │  convert_to_llm → provider payload → llm.chat (streamed)        [04 adapter]
  ▼
  LLM proposes: get_canonical_context(2330,0050)  [public]
  │  pre_tool hook: guardrails (no full-market scan) + entitlement  [10 · 05 guardrails]
  ▼  dispatch → tool → context pipeline → dataaccess router → (cached, no network)
  │  post_tool: journal + source-health; tool.result SSE frame      [07 streaming]
  ▼
  LLM proposes: get_chip_flows(2330)  [sponsor]
  │  entitlement (per-call): Free → masked teaser {locked, required_tier, preview}
  │  → Premium/Pro → full data
  ▼
  LLM composes final answer                                         [05 loop]
  │  pre_output hook: guardrails scan — no trading language, caveats present → pass
  ▼
Journal records every step; usage finalized (post_turn)            [10 usage]
  → streamed to caller as typed SSE events (caveat, turn.end, tool.result, …)  [07]
```
(This sequence is illustrative of the design; `tool.locked` and `message.delta` SSE frame kinds
are not yet emitted — see [modules/07](modules/07-conversation-streaming.md)'s
implementation-status note for the real/not-yet-built split.)

**Parallel / team variants ([11](modules/11-orchestration.md)):** for `subagent` the orchestrator
fans out N lanes (each its own Step loop with a skill's tool allowlist), every event tagged with
`agent_id`/`parent_agent_id` so the client renders parallel lanes; a **synthesis** pass merges
them and runs `pre_output` again (non-trading enforced per-lane AND at synthesis). `team` chains
lanes sequentially, handing forward the **structured finding**, not raw transcript. Mid-run user
input is a **steering** message injected at the next `step_start` (no cancel); `switch-branch`
regenerates from another message with a branch summary — all over the same SSE envelope ([07](modules/07-conversation-streaming.md)).

## 9. Repository layout

```
ai-tw-stock/
├─ docs/                       # this documentation set
└─ backend/
   ├─ src/
   │  ├─ agent/                # loop, planner, context_window, guardrails, prompts/
   │  ├─ orchestrator/         # intent classifier, single/subagent/team, synthesis
   │  ├─ llm/                  # base + anthropic/openai/gemini/openai_compat(groq,cerebras,
   │  │                        #   openrouter)/local(ollama) + registry, routing
   │  ├─ tools/                # base, registry, market, context, health tool groups
   │  ├─ dataaccess/           # capability registry + Market/Symbol + priority-fallback router (config-driven)
   │  ├─ sources/              # 13 adapters incl. finmind/, yfinance/, and tdcc/ packages (mops/ptt/etf_pcf scrapers, news, web_search) + base + tls — ONLY network I/O
   │  ├─ attachments/          # chat-attachment handling: image passthrough + markitdown-based document extraction
   │  ├─ calendar/             # events calendar: rule engine + feed/scraper composition, state model
   │  ├─ ingest/               # data-ingest/ETL: cron job groups, whole-market-by-date writers, backfill (§9c)
   │  ├─ analytics/            # derived compute: indicators, multi-factor 多空 score, AI summary/SWOT, screener, themes
   │  ├─ context/              # canonical, observation, conversation, source_health, builder
   │  ├─ skills/               # packaged analysis playbooks + registry, built-ins/
   │  ├─ conversation/         # store, session, streaming (SSE), history
   │  ├─ memory/               # working, session, long_term
   │  ├─ events/               # bus, hooks
   │  ├─ accounts/             # auth, users, profile, entitlements, byok, quotas
   │  ├─ payments/             # Paddle: plans, checkout, webhooks → tier/byok
   │  ├─ feedback/             # feedback events, quality scoring, alerts
   │  ├─ usage/                # accounting, rate limiting
   │  ├─ scheduler/            # jobs, alerts, delivery channels, worker
   │  ├─ voice/                # stt, tts adapters
   │  ├─ personality/          # agent-voice presets (穩健/白話/簡潔/數據派): registry + prompt fragments (style-only)
   │  ├─ pets/                 # cosmetic pet/gamification: catalog, gacha/pity, xp, persona hint (decoupled from tiers)
   │  ├─ schemas/              # pydantic models, JSON schemas, source contracts
   │  ├─ db/                   # ORM models, migrations, store implementations
   │  ├─ api/                  # FastAPI app + routers/
   │  ├─ mcp/                  # MCP server (FastMCP)
   │  └─ config/               # settings, watchlists, adapter matrix, tls, hooks, tiers
   ├─ tests/                   # profiled, non-network by default
   └─ pyproject.toml
```

## 9a. Interaction layer (rich chat backend)

The runtime is wrapped by a chat-backend layer — all under the same governance:

- **Orchestration** ([modules/11](modules/11-orchestration.md)): per-query LLM intent
  classifier → `none | single | subagent (parallel) | team (sequential)`; dynamic skill
  assignment; cost-tiered model routing. Decides single-vs-parallel so the caller doesn't.
- **Conversation & streaming** ([modules/07](modules/07-conversation-streaming.md)):
  persistent threads (create/list/get/fork/resume), typed SSE events, turn cancellation,
  and steering (real, mid-run — [modules/07](modules/07-conversation-streaming.md)). The
  `ask_user` **tool** (`tools/ask_user_tools.py`, `card_type = "ask_user"`) is real and built —
  it runs today as an ordinary read-only tool call like any other. What's *not* yet built is a
  dedicated `ask_user` SSE **event kind** with turn pause/resume semantics: the agent loop
  returns one whole `TurnResult` per turn, not the token/tool-level stream a live, mid-turn pause
  awaiting the user's answer would need; see [modules/07](modules/07-conversation-streaming.md)'s
  implementation-status note.
- **Skills** ([modules/08](modules/08-skills.md)): versioned analysis playbooks; triggered
  by the classifier or UI action chips (not slash-command-first).
- **Memory** ([modules/09](modules/09-memory.md)): working / session / long-term.
- **Events & hooks** ([modules/10](modules/10-events-hooks.md)): internal event bus (SSE is
  a projection), vetoing lifecycle hooks (guardrails are mandatory), usage/cost accounting +
  rate limiting.
- **Feedback** ([modules/13](modules/13-chat-interaction-features.md)): 👍/👎 → rolling
  skill quality → admin alerts.

These map onto the reference diagram's "controlled conversation context → safe artifact /
AI discussion handoff": a thread's journaled turns + Conversation package are the artifact.

## 9b. Product layer (multi-user hosted)

- **Accounts & tiers** ([modules/12](modules/12-multi-user-tiers.md)): OAuth, users (profile:
  birthday/phone+country code/nationality), tiers (free/premium/pro), single `entitlements`
  module, per-tier rate limits + LLM cost budgets, **a single static Redis-backed FinMind
  Sponsor pool (6000 req/hr) shared across all Premium/Pro users** — reserve-before-act with
  refund-on-failure/under-use, not a per-tier quota (Free never touches this pool at all; see
  [governance.md](governance.md) §9) — BYOK. Everything stateful is scoped by `user_id`.
- **Payments** ([modules/15](modules/15-payments-paddle.md)): **Paddle** subscriptions
  (Premium/Pro × monthly/annual × **BYOK/standard**, 8 price IDs); webhook is the source of
  truth for `tier`/`byok`; BYOK variant is cheaper.
- **Chat interaction features** ([modules/13](modules/13-chat-interaction-features.md)):
  feedback, retry/regenerate, edit-and-resend (branches in-tree via switch_branch), stop/cancel SSE, voice STT/TTS, full
  history management (rename/auto-title, delete, sort, star/pin, archive, search, tag, export).
- **Scheduler & alerts** ([modules/14](modules/14-scheduler-alerts.md)): user-authorized
  scheduled jobs + alerts + delivery — under the **governance carve-out** (§9a): read-only,
  non-streaming, non-trading, quota-bounded, coarse cadence.

## 10. Cross-cutting references

- **API reference** (all REST + SSE + MCP endpoints): [api-reference.md](api-reference.md)
- **Database schema** (all tables): [database-schema.md](database-schema.md)
- **Data catalog** (every source/dataset): [data-catalog.md](data-catalog.md)
- **Governance** (rules + amendments): [governance.md](governance.md)
- **Infrastructure & ops** (deployment, cache tier, Redis, observability): [modules/16-infrastructure.md](modules/16-infrastructure.md)
- **Module specs**: [modules/](modules/) · **Decisions**: [adr/](adr/)

## 11. Build phases

This list is the **design-time phase sequence**, not a live status board — for current
per-phase status (what's actually built, tested, and verified vs. still pending), see
[DEVELOPMENT.md §3](DEVELOPMENT.md#3-build-order-turn-the-blueprint-into-code).

1. Foundations (scaffold `backend/`, config, schemas, db, TLS + tier enums, docs)
2. Data layer (13 adapters incl. the FinMind [104 datasets], yfinance, and TDCC packages + **data-access router** (capabilities, Market/Symbol, per-market priority-fallback config) + contracts + fixtures)
3. Context pipeline (canonical/observation/conversation/source_health/builder) + calendar (rule engine + event composition)
3a. **Data ingest / ETL** ([modules/22](modules/22-data-ingest.md)): cron job groups, whole-market-by-date writers, idempotent upsert + gap-fill, backfill — populates the derived store (§9c). Precedes analytics.
3b. **Analytics & scoring** ([modules/20](modules/20-analytics-scoring.md)): indicator library, multi-factor 多空 score, AI summary/SWOT, screener + AI top-N, theme discovery — derived compute over the ingest store.
4. Tool registry (tiered read-only tools; incl. analytics tools)
5. LLM adapter (providers + registry + role routing)
6. Agent runtime (loop, planner, context_window, guardrails, journal)
7. Interaction layer (event bus + hooks, conversation store + SSE, memory, skills, feedback)
8. Orchestration (intent classifier, single/subagent/team, synthesis)
9. Product layer (accounts/profile/tiers/entitlements/BYOK, Paddle billing, chat features, voice, cosmetic pets/gamification — [modules/21](modules/21-pet-gamification.md), agent-voice personality presets — [modules/23](modules/23-agent-personality.md))
10. Scheduler & alerts (HA worker + leader election, job/alert store, at-least-once delivery — §9a)
11. Interfaces (FastAPI routers, then MCP/FastMCP server + MCP principal/entitlement)
12. **Infrastructure & operations** ([modules/16](modules/16-infrastructure.md) +
    [modules/17](modules/17-devops-operations.md)): Redis event transport + cache, DuckDB/Parquet
    market-data tier, migrations (`create_all` + additive upgrade now; Alembic from the first production release), observability/tracing, secrets/KMS,
    deployment topology, circuit breakers, cost ceilings — plus CI/CD, containers, probes,
    graceful SSE drain, DR, Redis HA, PII residency, runbooks, feature flags
13. Hardening (test profiles, governance + per-user isolation + context-efficiency +
    summary-quality + classifier-routing evals + load tests, docs finalization)
