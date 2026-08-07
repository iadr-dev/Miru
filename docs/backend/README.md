# AI TW Stock/ETF Agent Framework — Documentation

A professional, **Claude Code-style agentic framework** for Taiwan stock/ETF market
analysis, built on the governed, read-only data architecture of the reference project
[`tw-market-live-data-intelligence`](https://github.com/SmartyJohnway/tw-market-live-data-intelligence/).

The framework keeps the reference project's **context-only, non-trading, bounded-observation**
governance while adding a real **provider-agnostic LLM agent runtime** that treats every
data source as a governed tool, and serves results over a **read-only FastAPI API** and
**MCP server**.

## Read in this order

1. [architecture.md](architecture.md) — the full system blueprint: modules, data flow,
   repo layout, and the anatomy of a single agent turn.
2. [governance.md](governance.md) — the non-negotiable rules the whole system enforces:
   Mode A/B/C, Level 1/2, data-class entitlement gate (paid FinMind), TLS policy, non-trading guarantee.
3. [data-catalog.md](data-catalog.md) — verified inventory of every data source
   (TWSE/TPEX/TAIFEX OpenAPI + FinMind's 104 datasets), a CMoney-class gap analysis, and
   the direct/derive/paid build strategy.
4. [api-reference.md](api-reference.md) — consolidated REST + SSE + MCP endpoint reference.
5. [database-schema.md](database-schema.md) — all tables (accounts, threads, memory,
   feedback, usage, scheduler…).
6. [modules/](modules/) — one detailed spec per module.
7. [adr/](adr/) — architecture decision records explaining *why* key choices were made.

**Building the backend from these specs?** Start with **[DEVELOPMENT.md](DEVELOPMENT.md)** — the
developer runbook (quickstart, local infra, build order, test profiles, "how to add X" recipes, the
governance gate). It's the *how*; the docs above are the *what & why*. Coding standards live in
[`../../backend/CLAUDE.md`](../../backend/CLAUDE.md).

## Module map (mirrors the reference project's naming)

| Code | Name | Mode / Level | Role |
|------|------|--------------|------|
| M5F | Canonical Package | Mode A / Level 1 | Immutable preserved market context |
| M5K | Bounded Observation | Mode B / Level 2 | Explicit, operator-triggered live-ish check |
| M5N | Conversation Package | Mode C | AI-safe caveated artifact the agent reasons over |
| M5Q | Source Health | — | Data-provider currentness diagnostics |
| M8A/B/C | Multi-source Context Builder | — | Official EOD (A), derivatives (B), TAIFEX_MIS staged (C) |
| M6D | TLS Policy | — | `strict \| compatibility \| unsafe-explicit`, no silent fallback |
| — | Agent Runtime | — | **New:** LLM loop, planner, guardrails, context manager |
| — | Interaction Layer | — | **New:** threads + SSE, skills, memory, events/hooks |
| — | Orchestration | — | **New:** intent classifier → none/single/subagent/team, synthesis, model routing |
| — | Analytics & Ingest | — | **New:** data-ingest/ETL (§9c) populating the derived store; derived analytics — indicators, multi-factor 多空 score, AI summary/SWOT, screener, themes |
| — | Product Layer | — | **New:** multi-user auth + tiers, Paddle billing (BYOK-priced), chat features (feedback/retry/voice/history), scheduler/alerts, cosmetic pets/gamification, agent-voice personality presets |

## Confirmed design decisions

- **Agent model:** internal agentic loop (Claude Code-style), not just external handoff.
- **LLM:** provider-agnostic adapter — Anthropic, OpenAI, Gemini, Groq, Cerebras, OpenRouter,
  Ollama (local), swappable via config — with role-based, cost-tiered model routing.
- **Orchestration:** dynamic intent classifier routes each query to none/single/subagent/team.
- **Interfaces:** FastAPI (REST + SSE) product API + read-only MCP server (FastMCP, our own
  tools). Frontend (web/mobile client) is a separate deliverable, not yet specced here.
- **Access control:** all tools are read-only and freely runnable — **no safety tiers, no
  approval gate**. The only gate is **paid FinMind Sponsor data** (`data_class: sponsor`),
  gated by user tier (Premium/Pro) and shown to Free users as a masked/blurred teaser (馬賽克)
  with an upgrade CTA. See `governance.md` §4 and `adr/0010`.
- **Backend layout:** `backend/src/<domain>/` (agent, orchestrator, llm, tools, sources,
  dataaccess, calendar, ingest, analytics, context, conversation, memory, skills, events,
  accounts, payments, feedback, usage, scheduler, voice, personality, pets, schemas, db, api, mcp, config).
  Governance mode codes (M5F/K/N/Q…) are concepts; code uses domain filenames.
- **Analytics & ingest:** an `ingest/` ETL layer populates a derived DuckDB/Parquet store on a
  trading-calendar cron (governance §9c carve-out); an `analytics/` layer derives indicators, a
  multi-factor **多空** score, AI summary/SWOT, and an AI top-N screener over it — all
  non-trading, factor-transparent. See `modules/20`, `modules/22`, `adr/0023`, `adr/0025`.
- **Pets/gamification:** a **cosmetic-only** companion (gacha on register, XP/leveling, reactive
  in chat) **fully decoupled** from tiers/entitlements/analysis — never affects a
  trading-relevant output. See `modules/21` and `adr/0024`.
- **Deployment:** **multi-user hosted** — OAuth, tiers (free/premium/pro), entitlements, BYOK
  (amends the original local-first assumption; see `governance.md` §9 and `adr/0008`).
- **Scheduler:** user-authorized jobs/alerts permitted under a **governance carve-out**
  (`governance.md` §9a, `adr/0009`) — read-only, non-streaming, non-trading, quota-bounded.
- **Governance:** inherited from the reference project, with explicit amendments §9 and its
  **§9a–§9c** carve-outs (§9a user-authorized scheduler · §9b demand-driven live quotes · §9c
  platform data-ingest) — see `governance.md`.

## Status

Real, growing codebase (see [DEVELOPMENT.md §3](DEVELOPMENT.md#3-build-order-turn-the-blueprint-into-code)
for the per-phase build-order status) — no longer just a design/blueprint. No production
guarantee, no real-time guarantee, no trading recommendations — by design.
