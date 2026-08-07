# Backend — Developer Guide (build-from-blueprint)

The **on-ramp** between the spec and the code. The spec (`architecture.md`, `governance.md`,
`modules/*`, `adr/*`) is the **reference — what & why**; this doc is the **runbook — how**: how to
set up, in what order to build, how to test, how to add things, and the gate a change must pass.

> **Status: Phases 1–11 done, Phase 12 (infra & ops) mostly done, Phase 13 (hardening) not
> started.** `backend/` is now a real, growing codebase,
> not just a blueprint — see the build-order table below for per-phase status. This guide's
> *intended* setup/toolchain (matching [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md)) already
> matches the real repo for what's built. As code lands, keep this doc in sync (it should never
> contradict `backend/CLAUDE.md` or the module specs; when in doubt, **the spec wins** — fix the
> code, not the rule).

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
uv run python -m ingest.backfill_cli --yes   # seed a little market data into DuckDB/Parquet (dev)

uv run uvicorn api.app:app --reload     # the REST + SSE API  → http://localhost:8000
uv run python -m mcp                    # (separate shell) the standalone MCP process
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
brings up the full stack (**api + scheduler + ingest + mcp**, one image role-selected per service
via `SERVICE_ROLE` — there is no separate "worker" role — + Redis + Postgres + DuckDB/Parquet
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

| Phase | Build | Spec | Status |
|---|---|---|---|
| 1 | Foundations — scaffold `backend/src/`, `config/`, `schemas/`, `db/`, TLS + enums, `pyproject.toml` | [architecture.md §9](architecture.md#9-repository-layout) | ✅ Done |
| 2 | Data layer — the 12 source adapters + **data-access router** (capabilities, Market/Symbol, priority-fallback config) + contracts + **recorded fixtures** | [01](modules/01-sources.md), [19](modules/19-data-access-router.md) | ✅ Done (12/12: FinMind — 104 datasets across Technical/Chip/Fundamental/Derivative/Real-Time/CB/Others/International/Global-Economic — TWSE/TPEX/TAIFEX OpenAPI, TWSE MIS, NCDR closure, news, PTT, ETF PCF, yfinance (now full multi-market: TW gap-filler + non-TW primary), MOPS (via TWSE OpenAPI `t187ap04_L`/`t187ap11_L`, not the dead legacy HTML scraper), web_search — all live-verified against real upstreams, not just fixture-mocked; TPEX OpenAPI's endpoint is confirmed correct but currently unreachable under strict TLS due to a real defect in the upstream's own certificate, documented in-code) |
| 3 | Context pipeline (canonical/observation/conversation/source_health/builder) + calendar | [02](modules/02-packages.md), [18](modules/18-calendar-events.md) | ✅ Done |
| 3a | **Ingest/ETL** — cron job groups, whole-market-by-date writers, idempotent upsert + gap-fill, backfill (§9c) | [22](modules/22-data-ingest.md) | ✅ Done (price_eod + institutional_flow job groups wired; remaining datasets stubbed as a checklist, leader-election is a single-process stub pending Phase 12) |
| 3b | **Analytics** — indicator library, multi-factor 多空 score, AI summary/SWOT, screener + top-N, themes | [20](modules/20-analytics-scoring.md) | ✅ Done (indicator library + 5-factor score computable from price_eod/institutional_flow; `analytics/screener/` — the tag-flag batch + `run_tag_screener`/`get_tag_catalog` tools — landed later, see below; value/quality factors + AI summary/SWOT still deferred pending more ingest coverage + LLM integration). **Theme layer, Layer 1 landed:** `analytics/themes/` now exists (`models.py`/`store.py`/`seed.py`) with the 4 tables (`themes`/`theme_memberships`/`theme_tiers`/`supply_chain_edges`, migration `d4e5f6a7b8c9`) and a deterministic non-LLM seed that materializes industry-kind themes + memberships from FinMind's industry-chain classification roster, wired into the ingest daemon on a **monthly** cadence (new `Cadence` class on `ScheduledIngestJob`; `IngestContext` gained a `session_factory` seam so a relational-store job runs in the daemon) and writing a transactional replace-per-`(theme, source)` snapshot. **Layer 2 (tiers) now landed:** `theme_tiers` is populated by `seed_theme_tiers` (new `analytics/themes/tiers_seed.py` + pure classifier `tiers.py` + curated map `config/theme_tiers.yaml`), wired into the same monthly daemon pass immediately after the membership seed (tuple-ordered, same fire time) — curated editorial rows plus a keyword-heuristic backfill over every remaining membership sub-industry, idempotent and curated-wins on re-run (enforced both in the store's `upsert_theme_tier` and by the backfill skipping curated-covered pairs); store gained `get_theme_tiers`/`list_uncurated_sub_industries`/`upsert_theme_tier`. **Layer 3 (concept-theme LLM research + supply-chain edges) now landed, operator-triggered:** `analytics/themes/research.py` + `__main__.py` (`python -m analytics.themes`, a one-shot run-and-exit script, deliberately **not** wired into any scheduled ingest pass — it is the only layer that spends on LLM calls + live web research). For each curated concept theme (`config/concept_themes.yaml`, ~12 real TW market narratives — CoWoS先進封裝/AI伺服器/矽光子/…) it gathers web + local-cross-reference evidence, runs two independent researchers (two different model **roles**, subagent + agent), reconciles disputed picks via a verifier role (agreed→0.9, verifier-kept disputed→0.6, fail-open to the union on a verifier-call failure), and persists `concept`-kind themes/memberships with `source='concept_research'`. **The fabrication safeguard** (the governance-critical control): every model-proposed symbol is filtered **in code** against the real stored universe (all symbols in `theme_memberships`) before persistence — for both researchers before reconciliation and again on the reconciliation result incl. the fail-open path — so a hallucinated code never reaches a stored row; governance-marked test proves it. Supply-chain edges: a hand-seeded bootstrap YAML (`config/supply_chain_seed.yaml`, `source='seed'`) plus an in-theme-only LLM extractor (`source='llm_extraction'`) that may connect only a single theme's own members (out-of-theme edges rejected in code; governance-adjacent test proves it). New `theme_change_log` table (migration `e5f6a7b8c9d0` off `d4e5f6a7b8c9`) audits every write under one `run_id`; store gained `list_all_membership_symbols`/`find_symbols_by_sub_industry_keyword`/`replace_edges_for_source`/`get_edges_from`/`record_change`. **Tag screener landed later:** `analytics/screener/batch.py::job_screener_flags` (daemon-wired at 20:30) + `store.py::run_tag_screener_query` back the `run_tag_screener`/`get_tag_catalog` tools over the Parquet reader; direct consumption of `get_theme_members`/`get_theme_tiers`/the edge graph by the screener itself remains a future refinement |
| 4 | Tool registry — tiered read-only tools (immediate core + deferred index) | [03](modules/03-tools.md) | ✅ Done (immediate/deferred core wired against real capabilities; tools needing missing adapters/analytics explicitly skipped, not stubbed) |
| 5 | LLM adapter — providers + registry + role-based routing | [04](modules/04-llm-adapter.md) | ✅ Done (all 7 providers: Anthropic, OpenAI, Gemini, Groq/Cerebras/OpenRouter via shared base, Ollama) |
| 6 | Agent runtime — loop, planner, context_window, **guardrails**, journal | [05](modules/05-agent-runtime.md) | ✅ Done (Step loop, guardrails non-trading/full-market-scan checks, compaction preserving caveats, structlog journal; personality/skill/pet prompt-layer seams left empty at this phase, filled in Phase 9 — see that row) |
| 7 | Interaction layer — event bus + hooks, conversation store + SSE, memory, skills, feedback | [07](modules/07-conversation-streaming.md)–[10](modules/10-events-hooks.md), [13](modules/13-chat-interaction-features.md) | ✅ Done (`conversation/` — Thread/Turn/Message store + branch summarization + turn lifecycle; `events/` — `EventBus`/hooks/usage/rate-limit; `memory/` — store + watchlists + recall; `skills/` — registry + 3 built-in skills wired to real tools; first Alembic migration, `0001_phase7_conversation_memory_events`). **`rich_card` SSE event landed later** (a targeted fix, not part of the original phase build): a per-tool `card_type` opt-in (`tools/base.py`), emitted on `single`/`subagent`/`team` alike, governed via a structured non-trading scan, verified never built from a masked/unentitled `sponsor` result. The doc audit that found this gap also found the SSE vocabulary is otherwise still ~90% unbuilt (`message.delta`/`tool.*`/`activity`/`ask_user`/`pet.*` — see [07](modules/07-conversation-streaming.md)'s own implementation-status note) — the agent loop returns one whole `TurnResult` per turn, not a token/tool-level stream; closing that is separate, larger future work, not implied by this fix |
| 8 | Orchestration — intent classifier → none/single/subagent/team, synthesis | [11](modules/11-orchestration.md) | ✅ Done (classifier + all 4 frameworks + synthesis incl. partial/conflicting/failure handling + §3b ceilings, wired to real `agent/`/`skills/`/`llm/` — no migration needed, `ck_turns_framework` already covers it; quota pre-flight reservation is a `QuotaReserver` Protocol seam, since filled by `usage/finmind_pool.py`'s `RedisFinMindPoolReserver` — see Phase 12; mid-flight steering injection deferred at this phase, filled later via `agent/loop.py`'s `SteeringSource` — see Phase 11's `steer` note) |
| 9 | Product layer — accounts/tiers/entitlements/BYOK, chat features, voice, cosmetic pets, personality presets, **Paddle billing** | [12](modules/12-multi-user-tiers.md), [15](modules/15-payments-paddle.md), [21](modules/21-pet-gamification.md), [23](modules/23-agent-personality.md) | ✅ Done (`accounts/` — real Google + LINE OAuth 2.0 authorization-code flow (`oauth_providers.py`/`oauth_flow.py`): Google via `state`-only CSRF + JWKS/RS256 ID-token verification (`PyJWKClient`), LINE via PKCE(S256)+`state`+`nonce` + the `/verify` endpoint; multi-provider account linking (Google+LINE coexisting on one account) verified end to end; FastAPI routes wired in Phase 11. `personality/` (4 presets), `pets/` (15-pet catalog, gacha/pity/XP/streak), `voice/` (transcribe/speak, no turn creation) done. **`payments/` — Paddle billing now done** (picked back up after being deliberately deferred): the 8-price-ID plan mapping, an async Paddle API client (sandbox/production base URL), `Paddle-Signature` verification (`{ts}:{raw_body}` HMAC-SHA256, `hmac.compare_digest`, 5s replay tolerance — verified byte-exact against re-serialization), webhook idempotency (`billing_events.paddle_event_id` unique) + an out-of-order-event guard so a late-delivered stale webhook can't regress `tier`, reconcile-on-read self-healing, and the 6 `/billing/*` endpoints; `users.tier` is written only by the webhook, never client-asserted; migration `b2c3d4e5f6a7` off Phase 10's head) |
| 10 | Scheduler & alerts — HA worker + leader election, job/alert store, at-least-once delivery (§9a) | [14](modules/14-scheduler-alerts.md) | ✅ Done (`scheduler/` — job/alert/preference/notification/device-token store + models; leader-gated worker reusing `ingest/leader.py`'s `LeaderElection` Protocol (not reinvented); scheduled runs go through a new `Orchestrator.run_skill()` — skips the intent classifier since a job already names its skill, but reuses the exact single-lane machinery + mandatory `pre_output` guardrail; entitlement re-resolved fresh at run time with auto-disable on lapse, quota-exceeded skips-and-reports never retry-loops, `AlertCondition` modeled as a structured threshold/streak schema rather than free-text to avoid a prompt-injection surface; delivery channels: in-app fully real, LINE-Messaging/Telegram/email/APNs-FCM have real request-shaping with the live socket write left as a marked seam behind `sources/` per the network-I/O boundary, consistent with `voice/providers.py`'s stub convention; new migration off Phase 9's head, dual-dialect, idempotent-delivery dedupe constraint; `/schedules`/`/alerts`/`/notifications`/`/me/devices` routes wired into `api/app.py`, Pro-tier gated) |
| 11 | Interfaces — FastAPI routers, then MCP/FastMCP server + principal/entitlement | [06](modules/06-interfaces.md) | ✅ Done, with real gaps flagged below — MCP done (`mcp/app.py`: FastMCP 3.x, 5 immediate tools + `search_tools`/`execute_tool` over the deferred 92-tool tail (registry now 97 tools total — see [03](modules/03-tools.md)), `principal.py` entitlement seam, `canonical://`/`health://` resources, `/healthz`, Origin/`MCP-Protocol-Version` hardening; `python -m mcp` is now the real, live-verified production entry point — §1). FastAPI surface built out fully against `api-reference.md`'s spec, including `/analytics/*` (module 20's score/rankings endpoints, landed later) and `/billing/*` (Paddle, landed later): OAuth login/link/refresh, thread CRUD + fork/search/export/rename/star/archive/tag (`If-Match` CAS → 409), turn-lifecycle (`switch-branch`/`cancel` real via a `TurnRegistry`; `feedback`/`regenerate`/`edit` are now real too, backed by `feedback/store.py` and `api/turn_driver.py` — landed later, see the doc audit note below), voice transcribe/speak, market/derivatives/macro REST (8 of 12 named routes — the other 4 skipped because their named tools were verified to not exist: `get_market_context`/`get_advance_decline`/`get_index_contribution`/`get_sector_indices`), pets, memory/watchlists (reusing the existing lexorank ordering), `/me` profile + personality presets + BYOK key store (never logs the raw key, tested). **Known real gaps, not yet closed:** `/search` symbol type-ahead only covers non-TW markets (no ingested TW symbol-universe search service); `GET /threads/{id}/stream` only supports live-attach, not durable `Last-Event-ID` replay (closed later — see Phase 12); `/usage`/`/usage/summary` unbuilt (`events/usage.py` is an in-process accumulator with no durable rollup store). **Landed later, closing this phase's original gaps:** `steer` now actually redirects a running turn (`agent/loop.py`'s `SteeringSource`/`_inject_steering`, polled at each Step boundary — not just recorded); `/updates/stream` now relays a real `data.updated` signal (Phase 12); personality (`orchestrator.orchestrator.PersonalityContext`/`_resolve_personality_fragment`) is wired into both the single-lane and fan-out synthesis paths, not an empty seam |
| 12 | Infra & ops — Redis transport + cache, DuckDB/Parquet tier, Alembic dual-dialect, observability, deploy | [16](modules/16-infrastructure.md), [17](modules/17-devops-operations.md) | 🔄 Partial — first slice done: **Redis-backed cross-process `EventBus`** (`events/redis_bus.py` + `events/stream_store.py`) with durable `stream_events` replay (closes Phase 11's flagged gap — `GET /threads/{id}/stream` now does real `Last-Event-ID` resume: replay-then-live-attach with no gap and no duplicate at the boundary), config-selected via `event_bus_backend` (`memory`|`redis`), tested against `fakeredis` in the default profile (no live Redis required); **read-only DuckDB/Parquet reader** (`ingest/reader.py`) for API workers over the exact writer layout from Phase 3a, never touching the writer's `.duckdb` file, built as a standalone tested module rather than wired into `context/` directly since `context/` is barred from opening DuckDB itself per the dependency rule — the formal `MarketDataStore` Protocol + its `sources/`-side caching adapter remain unbuilt and are the real next step to make this reader load-bearing on the live request path. **Also fixed while verifying this pass:** `api/routers/agent.py` was generating an unrelated `uuid4()` as the streaming `turn_id` instead of using the persisted turn's real id, so every durable-log write for a live agent turn silently no-op'd against the `stream_events.turn_id` FK — corrected to use `append_turn`'s returned `Turn.id`. Second slice done: **Redis-backed rate limiting + quota enforcement**, all four wired into their real call sites (verified independently, not just unit-tested) — a per-user rate limiter (atomic `MULTI`/`EXEC` `INCR`+`EXPIRE` pipeline) gating `/agent/query`'s pre-turn check; a durable daily-chat-quota counter (new `usage_counters`/`usage_events` tables, migration `c3d4e5f6a7b8`, dialect-aware `INSERT...ON CONFLICT` reserve-before-act — deliberately Postgres/SQLite not Redis, since a hard cap must survive a cache flush) also gating the pre-turn check and fed by a now-actually-wired `UsageAccumulator` (previously built but never called from any real path — same inert-feature pattern as the `turn_id` bug, caught proactively this time); a global (not per-user) FinMind Sponsor-pool reserve-and-refund (`usage/finmind_pool.py`, atomic `INCRBY`/`DECRBY`) wired into the orchestrator's fan-out; the scheduler's quota gate now defaults to the real usage-backed gate instead of the permissive stub. Third slice done: **live-quote demand-driven poller + `data.updated` broker** — `GET /quote/stream` rewired from a naive per-connection poll to a shared, Redis-refcounted poller (`events/live_quote.py`): N subscribers to one symbol cost one upstream `get_price_quote` fetch, not N, verified end-to-end through a real ASGI harness (`TestClient` can't stream unbounded SSE); per-worker poller model (not a second leader-election domain) with the fastest-requested cadence winning per symbol so no subscriber is starved; disconnect correctly drops the demand refcount. `GET /updates/stream` now relays a real `data.updated` signal the ingest leader publishes after a successful batch write (leader-gated by the same `LeaderElection.is_leader()` check that already gates the write itself — a follower never emits), closing Phase 11's other flagged gap. Built `calendar/market_hours.py` (TWSE session window + the existing day-level trading calendar) since no market-open signal existed anywhere in the codebase. **Landed later, closing this phase's remaining gaps:** `docker-compose.yml` (4 real `SERVICE_ROLE`s — api/scheduler/ingest/mcp, one image — with Postgres/Redis health-gated startup and per-role healthchecks, live-verified against a real running container: `/healthz` on the `mcp` role returns 200, a real MCP protocol handshake at `/mcp` succeeds) and `.github/workflows/ci.yml` (lint/format/mypy/pyright/default-pytest/governance-pytest in one job, a separate dual-dialect-migrations + concurrency Postgres job, and a gitleaks secret-scan job — no pip-audit/Trivy/SBOM/nightly/canary-deploy job exists, that scope remains out of reach). Still deferred: observability/tracing, secrets/KMS, DR runbooks |
| 13 | Hardening — test profiles + governance/isolation/context-efficiency/summary-quality/routing evals | §5 below | ⬜ Not started |

**Post-Phase-2 data-layer expansion:** FinMind/yfinance were widened well past their original
Phase-2 scope (8→104 FinMind datasets; yfinance 3 TW fields→full non-TW primary source) and MOPS
landed via TWSE OpenAPI. **Tool expansion** (7→97 tools against the newly-available datasets, incl.
the theme/score/screener/ETF-overlap groups) and **doc sync** (`03-tools.md`, `data-catalog.md`,
`08-skills.md`, and other module docs corrected against real code) have both since landed. Most of
the long tail now has a skill allowlist home too (see the "Catch-all skills" table in
[08](modules/08-skills.md)). Remaining backlog, not yet closed: ingest job coverage (9/77
whole-market-eligible datasets wired, real daemon-fired via `ingest/daemon.py` — the remaining
~14 job groups are enumerated as a documented checklist in `ingest/jobs.py`'s
`STUBBED_DATASETS`, see [22](modules/22-data-ingest.md) §3); `analytics/` is TW-only and still
missing its `summary/` subpackage (its `screener/`/`themes/`/`scoring/` subpackages now exist —
see Phase 3b); `calendar/rules.py` ignores
its own `market` parameter (TW logic runs regardless of market) — see [22](modules/22-data-ingest.md),
[20](modules/20-analytics-scoring.md), [18](modules/18-calendar-events.md) for verified detail.

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
`orchestrator/` + `conversation/` + `events/` + `memory/` + `skills/` + `feedback/` + `usage/` +
`personality/` + `pets/` (interaction — per the enforced layer list in
`tests/arch/test_layering.py`, not the product layer) ·
`accounts/` + `payments/` + `scheduler/` + `voice/` (product) ·
`db/` · `api/` + `mcp/` (interfaces) · `config/` · `schemas/` · `tests/`.

## Cross-references

- Reading order + module map: [README.md](README.md)
- What & why: [architecture.md](architecture.md) · [governance.md](governance.md) · [adr/](adr/)
- Contracts: [api-reference.md](api-reference.md) · [database-schema.md](database-schema.md) · [data-catalog.md](data-catalog.md)
- Coding standards (agent-facing): [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md)
