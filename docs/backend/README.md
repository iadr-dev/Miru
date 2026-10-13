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
2. [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §4/§8 and [`../../AGENTS.md`](../../AGENTS.md) — the
   non-negotiable rules the whole system enforces (non-trading output, data-class entitlement gate,
   TLS policy, untrusted external content); [modules/25](modules/25-governance-enforcement.md) maps
   each rule to the code that enforces it.
3. [api-reference.md](api-reference.md) — consolidated REST + SSE + MCP endpoint reference.
4. [modules/](modules/) — one detailed spec per module. The data sources are the adapters in
   `backend/src/sources/`, and the tables are the models in `backend/src/db/models/`.

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
  with an upgrade CTA. See [modules/25](modules/25-governance-enforcement.md).
- **Backend layout:** `backend/src/<domain>/` (agent, orchestrator, llm, tools, sources,
  dataaccess, calendar, ingest, analytics, context, conversation, memory, skills, events,
  accounts, payments, feedback, usage, scheduler, voice, personality, pets, schemas, db, api, mcp, config).
  Governance mode codes (M5F/K/N/Q…) are concepts; code uses domain filenames.
- **Analytics & ingest:** an `ingest/` ETL layer populates a derived DuckDB/Parquet store on a
  trading-calendar cron (the platform-ingest carve-out); an `analytics/` layer derives indicators, a
  multi-factor **多空** score, AI summary/SWOT, and an AI top-N screener over it — all
  non-trading, factor-transparent. See `modules/20` and `modules/22`.
- **Pets/gamification:** a **cosmetic-only** companion (gacha on register, XP/leveling, reactive
  in chat) **fully decoupled** from tiers/entitlements/analysis — never affects a
  trading-relevant output. See `modules/21`.
- **Deployment:** **multi-user hosted** — OAuth, tiers (free/premium/pro), entitlements, BYOK
  (see `modules/12`).
- **Scheduler:** user-authorized jobs/alerts permitted under a **governance carve-out**
  (see `modules/14`) — read-only, non-streaming, non-trading, quota-bounded.
- **Governance:** inherited from the reference project, with three explicit carve-outs: the
  user-authorized scheduler, demand-driven live quotes, and platform data-ingest — see
  [modules/25](modules/25-governance-enforcement.md).

## Status

Real, growing codebase (see [DEVELOPMENT.md §3](DEVELOPMENT.md#3-build-order-turn-the-blueprint-into-code)
for the per-phase build-order status) — no longer just a design/blueprint. No production
guarantee, no real-time guarantee, no trading recommendations — by design.
