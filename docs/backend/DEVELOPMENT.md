# Backend — Developer Guide (build-from-blueprint)

The **on-ramp** between the spec and the code. The spec (`architecture.md`, `governance.md`,
`modules/*`, `adr/*`) is the **reference — what & why**; this doc is the **runbook — how**: how to
set up, in what order to build, how to test, how to add things, and the gate a change must pass.

> **Status: no code exists yet.** `backend/` is a design blueprint. This guide describes the
> *intended* setup/toolchain (matching [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md)) and the
> path to turn the specs into the real FastAPI backend — not a tour of a running system. As code
> lands, keep this doc in sync (it should never contradict `backend/CLAUDE.md` or the module specs;
> when in doubt, **the spec wins** — fix the code, not the rule).

**Before touching anything:** read [architecture.md](architecture.md) (layering + repo layout +
turn anatomy), [governance.md](governance.md) (the non-negotiables), and
[`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) (coding standards — §3 invariants, §4
governance-as-code, §5 concurrency). Governance rules are **load-bearing — never relaxed to make
code pass**.

---

## 1. Quickstart

Toolchain (2026 baseline, from [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §1): **Python
3.13**, **uv** (sole packaging/venv/lock), **ruff** (lint+format), **pyright** strict (+ `mypy` CI
second opinion), **pytest**.

```bash
# from repo root
cd backend
uv sync --all-extras            # create/refresh venv from uv.lock (commit the lock)
cp .env.example .env            # then fill in secrets (§2) — .env is gitignored, NEVER commit

uv run alembic upgrade head     # create the dev SQLite app DB
uv run python -m aitw.ingest --dev --once   # seed a little market data into DuckDB/Parquet (dev)

uv run uvicorn aitw.api.app:app --reload     # the REST + SSE API  → http://localhost:8000
uv run python -m aitw.mcp.server             # (separate shell) the MCP server
```

Smoke-test a non-AI public read (no key needed):

```bash
curl 'http://localhost:8000/api/v1/health'                 # source-health matrix
curl 'http://localhost:8000/api/v1/quote?symbols=2330&market=TW'   # EOD quote (public)
```

An AI turn (needs a provider key in `.env`, §2) — streamed SSE:

```bash
curl -N -X POST 'http://localhost:8000/api/v1/agent/query' \
  -H 'content-type: application/json' \
  -d '{"message":"台積電最近的籌碼？"}'
```

**Local-parity alternative — `docker-compose`** ([modules/17](modules/17-devops-operations.md) §2)
brings up the full stack (api + scheduler-leader + worker + Redis + Postgres + DuckDB/Parquet
volume), so the Redis-transport + one-writer-DuckDB paths are exercised locally, not first
discovered in prod:

```bash
docker compose up            # full stack on Postgres+Redis (prod-like)
docker compose --profile ollama up   # + local Ollama (offline LLM path)
```

Use SQLite + `uv run` for fast iteration; use compose before merging anything touching Redis
transport, the market-data store, or concurrency ([modules/16](modules/16-infrastructure.md) §10).

---

## 2. Local infrastructure & secrets

| Piece | Dev | Prod | Notes |
|---|---|---|---|
| **App DB** (users, threads, memory, usage, watchlists…) | **SQLite** file | **Postgres** | one schema, dual-dialect ([database-schema.md](database-schema.md)); `PRAGMA foreign_keys=ON` per SQLite conn or cascades no-op |
| **Market-data store** (EOD/history, scores, ETF holdings) | **DuckDB/Parquet** dir | same + more Parquet | leader writes, workers read-only Parquet ([modules/16](modules/16-infrastructure.md) §3) — **never** open the writer's `.duckdb` from a worker |
| **Event transport + cache** | Redis (compose) or in-proc (single-proc dev) | Redis | SSE fan-out, single-flight, quotas, OAuth-flow state ([modules/16](modules/16-infrastructure.md) §2/§3a) |

**Secrets — [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §8.** `.env` is **gitignored** and
blocked from writes by the repo's PreToolUse hook. **Never read `.env` values aloud, echo, log, or
commit them.** Env var *names* (values live only in `.env`; see [modules/16](modules/16-infrastructure.md) §8):

- **LLM (≥1 to run an AI turn):** the provider keys per [modules/04](modules/04-llm-adapter.md)
  (Anthropic/OpenAI/Gemini/Groq/Cerebras/OpenRouter; Ollama = keyless local). Provider chosen by
  config/env, never hardcoded.
- **Data:** `FINMIND_API_KEY` (only if exercising `sponsor` datasets locally — public reads work
  without it), `FINNHUB_KEY`, `BRAVE_SEARCH_API_KEY`. `ALPHA_VANTAGE_API_KEY`/`FRED_KEY`/`FMP_API_KEY`
  are **reserved for phased US/macro adapters** — not wired yet.
- **Auth/billing:** `JWT_SECRET_KEY`, Google/LINE OAuth creds, `PADDLE_*` (staging = Paddle sandbox).
- **Channels:** `TELEGRAM_BOT_TOKEN`, LINE messaging creds (distinct from LINE Login).

**No startup network calls in prod** ([modules/17](modules/17-devops-operations.md)):
`INGEST_ON_STARTUP` is **dev-only**; prod boots clean and the leader scheduler owns ingest.

---

## 3. Build order (turn the blueprint into code)

Follow the phase sequence in [architecture.md §11](architecture.md#11-build-phases) — each phase
links its module spec; build in order because each layer depends only on lower ones
([`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §3 **DEPENDENCY RULE**:
`interfaces → product → interaction → runtime → tools → context → dataaccess → sources`).

| Phase | Build | Spec |
|---|---|---|
| 1 | Foundations — scaffold `backend/src/`, `config/`, `schemas/`, `db/`, TLS + enums, `pyproject.toml` | [architecture.md §9](architecture.md#9-repository-layout) |
| 2 | Data layer — the 14 source adapters + **data-access router** (capabilities, Market/Symbol, priority-fallback config) + contracts + **recorded fixtures** | [01](modules/01-sources.md), [19](modules/19-data-access-router.md) |
| 3 | Context pipeline (canonical/observation/conversation/source_health/builder) + calendar | [02](modules/02-packages.md), [18](modules/18-calendar-events.md) |
| 3a | **Ingest/ETL** — cron job groups, whole-market-by-date writers, idempotent upsert + gap-fill, backfill (§9c) | [22](modules/22-data-ingest.md) |
| 3b | **Analytics** — indicator library, multi-factor 多空 score, AI summary/SWOT, screener + top-N, themes | [20](modules/20-analytics-scoring.md) |
| 4 | Tool registry — tiered read-only tools (immediate core + deferred index) | [03](modules/03-tools.md) |
| 5 | LLM adapter — providers + registry + role-based routing | [04](modules/04-llm-adapter.md) |
| 6 | Agent runtime — loop, planner, context_window, **guardrails**, journal | [05](modules/05-agent-runtime.md) |
| 7 | Interaction layer — event bus + hooks, conversation store + SSE, memory, skills, feedback | [07](modules/07-conversation-streaming.md)–[10](modules/10-events-hooks.md), [13](modules/13-chat-interaction-features.md) |
| 8 | Orchestration — intent classifier → none/single/subagent/team, synthesis | [11](modules/11-orchestration.md) |
| 9 | Product layer — accounts/tiers/entitlements/BYOK, Paddle billing, chat features, voice, cosmetic pets, personality presets | [12](modules/12-multi-user-tiers.md), [15](modules/15-payments-paddle.md), [21](modules/21-pet-gamification.md), [23](modules/23-agent-personality.md) |
| 10 | Scheduler & alerts — HA worker + leader election, job/alert store, at-least-once delivery (§9a) | [14](modules/14-scheduler-alerts.md) |
| 11 | Interfaces — FastAPI routers, then MCP/FastMCP server + principal/entitlement | [06](modules/06-interfaces.md) |
| 12 | Infra & ops — Redis transport + cache, DuckDB/Parquet tier, Alembic dual-dialect, observability, deploy | [16](modules/16-infrastructure.md), [17](modules/17-devops-operations.md) |
| 13 | Hardening — test profiles + governance/isolation/context-efficiency/summary-quality/routing evals | §5 below |

**The vertical-slice alternative:** if you'd rather ship an end-to-end path early, build a thin
slice through phases 2→4→5→6→11 (one public capability → one tool → LLM → loop → `/agent/query`)
with everything else stubbed, then widen. Either way the **dependency rule + governance hooks are
not deferrable** — they go in from the first slice.

---

## 4. The two commandments a change must honor

Everything in [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §3–§5 applies; these two cause the
most rework if missed:

1. **Only `sources/` does network I/O** ([adr/0002](adr/0002-tool-mediated-io.md)). Callers request a
   **capability + (symbol, market)** from the data-access router — never a source id, never an HTTP
   client outside `sources/`. The Redis/DuckDB cache is a caching adapter *inside* `sources/`.
2. **Governance + entitlement are mandatory, non-skippable hooks** ([adr/0018](adr/0018-enforcement-seam-and-store-protocols.md),
   [modules/10](modules/10-events-hooks.md) §2). `guardrails.check_output` blocks buy/sell/target/
   sizing on **every** sub-agent output *and* final synthesis; `data_class` is resolved **per serving
   adapter** and gated **per tool call** (mid-turn tier change is safe). A persona/pet/skill layer may
   style tone only — it can never drop a caveat or emit a signal.

---

## 5. Testing runbook

Non-network by default ([`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §7); CI's default suite
hits **no** upstream. Profiles are pytest markers:

```bash
uv run pytest                          # default = -m 'not network' (CI default; recorded fixtures)
uv run pytest -m governance            # HIGHEST priority — the guardrail/entitlement suite (§ below)
uv run pytest -m concurrency           # shared-state races (needs Postgres+Redis) — module 16 §10
uv run pytest -m dual_dialect          # every migration applies on BOTH SQLite and Postgres
uv run pytest -m operator_preflight    # live source fetch — MANUAL, opt-in only
uv run pytest -m browser_e2e           # scraper live path — MANUAL

uv run ruff check --fix . && uv run ruff format .   # lint + format
uv run pyright && uv run mypy src                   # types (both must pass on new code)
uv run pre-commit run -a                            # ruff + pyright + governance-fast subset
```

**The `governance` suite is the make-or-break** — it is tested directly against
[governance.md](governance.md): non-trading block, full-market-scan reject, caveat presence, sponsor
masking (public vs sponsor per serving adapter), canonical immutability, TLS no-silent-downgrade,
per-user isolation (no cross-`user_id` reads), prompt-injection resistance. Phase-13 also adds
context-efficiency, summary-quality, and classifier-routing evals ([architecture.md §11](architecture.md#11-build-phases)).

**Write tests as you build:** recorded fixtures per source adapter (live fetch only under
`operator_preflight`/`browser_e2e`); a **stub `LLMClient`** (deterministic scripted tool-calls) for
all runtime/orchestrator tests — never a real provider in the default suite.

---

## 6. "How to add X" recipes

Each is a thin change *because* the architecture is seam-based. Follow the owning spec; the recipe is
the checklist.

**Add a data source** → [modules/01](modules/01-sources.md), [adr/0002](adr/0002-tool-mediated-io.md)
1. New adapter in `sources/` implementing `SourceAdapter` (`fetch → validate → typed model`), tagging
   results with `source_id`, `timing_class`, `data_class`, `fetched_at`, `currentness_caveat`.
2. Declare its `supports() → {(capability, market)}`.
3. Add its id to the relevant `(market, capability)` lists in `config/source_priority.yaml`
   ([modules/19](modules/19-data-access-router.md) §4). **No caller code changes.**
4. Recorded fixture + adapter test. TLS via `sources/tls.py` (strict default, no silent downgrade).

**Add a tool** → [modules/03](modules/03-tools.md)
1. Implement the `Tool` protocol (`name`, JSON input schema, `run()`), wrapping the router/context —
   never touching network/files directly.
2. Register it; decide **immediate** (always-sent core — the canonical set is defined once in
   [modules/03](modules/03-tools.md)) vs **deferred** (long-tail, loaded on demand). Default deferred.
3. If it serves `sponsor` data, it returns the masked teaser envelope for unentitled users — the
   `pre_tool` entitlement hook handles it; don't hand-roll the gate.

**Add a skill (analysis playbook)** → [modules/08](modules/08-skills.md)
1. A `SKILL.md` (our frontmatter: name/version/triggers/tools/output_structure/freshness_rule/
   boundaries) in `skills/`. Its `tools` allowlist pre-loads exactly its deferred tools.
2. Guardrails still apply to its output — a trigger like 該買嗎 must reframe to descriptive factor
   context, never emit a signal.

**Add a capability / a new market** → [modules/19](modules/19-data-access-router.md), [adr/0020](adr/0020-multi-market-data-access.md)
- New **capability**: define it, then list `(market → ordered adapters)` in `source_priority.yaml`;
  adapters declare support. Cache keys off `(capability, market, symbol[, date])` automatically.
- New **market** (JP/GB/…): a `MarketProfile` + a priority block + a calendar rule-set — **zero caller
  changes** (this is why the router exists).

**Add an API endpoint** → [modules/06](modules/06-interfaces.md), [api-reference.md](api-reference.md)
- Add the router under `api/routers/`; **api-reference.md is the authoritative endpoint list** — keep
  it in sync. If it implies a new table, add it to [database-schema.md](database-schema.md) (dual-
  dialect: text UUID PK, JSON not `text[]`, CHECK not ENUM, money as int micro-units) + an Alembic
  expand-contract migration.

**Add a DB table/column** → [database-schema.md](database-schema.md), [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §6
- Every stateful table carries `user_id` (isolation + RLS). Add the concurrency guard its hot path
  needs ([modules/16](modules/16-infrastructure.md) §10 matrix: partial-unique, `version` CAS,
  idempotency key, reserve-before-act…). Migration must pass `-m dual_dialect`.

---

## 7. The governance gate (before every merge)

- **Run the domain gate:** the **`governance-reviewer`** agent (`.claude/agents/`) reviews a diff
  under `backend/src/` against [governance.md](governance.md) + [architecture.md](architecture.md) +
  [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §3–§5 — the check a generic code review misses.
- **CI must be green:** default `pytest` + `governance` + (if migrations touched) `dual_dialect` +
  (if shared state touched) `concurrency`; ruff + pyright + mypy clean.
- **Migration safety:** expand → deploy → contract-later; never a breaking in-place migration
  ([modules/17](modules/17-devops-operations.md) §1).
- **Branch, don't push to default; commit/push only when asked.** A change that weakens a
  non-negotiable to pass is a **reject**, not a merge — fix the code.

---

## 8. Where things live (quick map)

Full layout: [architecture.md §9](architecture.md#9-repository-layout). Most-touched dirs:

`sources/` (I/O only) · `dataaccess/` (capability router) · `context/` (M5F/K/N/Q packages) ·
`ingest/` + `analytics/` (derived store) · `tools/` · `llm/` · `agent/` (loop + guardrails) ·
`orchestrator/` · `conversation/` + `events/` + `memory/` + `skills/` (interaction) ·
`accounts/` + `payments/` + `usage/` + `scheduler/` + `voice/` + `personality/` + `pets/` (product) ·
`db/` · `api/` + `mcp/` (interfaces) · `config/` · `schemas/` · `tests/`.

## Cross-references

- Reading order + module map: [README.md](README.md)
- What & why: [architecture.md](architecture.md) · [governance.md](governance.md) · [adr/](adr/)
- Contracts: [api-reference.md](api-reference.md) · [database-schema.md](database-schema.md) · [data-catalog.md](data-catalog.md)
- Coding standards (agent-facing): [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md)
