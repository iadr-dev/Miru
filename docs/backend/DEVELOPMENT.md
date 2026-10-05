1	# Backend — Developer Guide (build-from-blueprint)
2	
3	The **on-ramp** between the spec and the code. The spec (`architecture.md`, `governance.md`,
4	`modules/*`, `adr/*`) is the **reference — what & why**; this doc is the **runbook — how**: how to
5	set up, in what order to build, how to test, how to add things, and the gate a change must pass.
6	
7	> **Status: a working backend exists.** `backend/` is no longer just a design blueprint — it now
> has a real, working FastAPI backend (30+ routers, 100+ tools, a full ORM, a real MCP server,
> ~200 commits of history). This guide describes the real
8	> setup/toolchain (matching [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md)); §3's build-order
9	> phase table below is kept as reference for how the system got built and what remains open — not
10	> a claim that nothing exists yet. Keep this doc in sync with the code (it should never contradict
11	> `backend/CLAUDE.md` or the module specs; when in doubt, **the spec wins** — fix the code, not the rule).
12	
13	**Before touching anything:** read [architecture.md](architecture.md) (layering + repo layout +
14	turn anatomy), [governance.md](governance.md) (the non-negotiables), and
15	[`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) (coding standards — §3 invariants, §4
16	governance-as-code, §5 concurrency). Governance rules are **load-bearing — never relaxed to make
17	code pass**.
18	
19	---
20	
21	## 1. Quickstart
22	
23	Toolchain (2026 baseline, from [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §1): **Python
24	3.13**, **uv** (sole packaging/venv/lock), **ruff** (lint+format), **pyright** strict (+ `mypy` CI
25	second opinion), **pytest**.
26	
27	```bash
28	# from repo root
29	cd backend
30	uv sync --all-extras            # create/refresh venv from uv.lock (commit the lock)
31	cp .env.example .env            # then fill in secrets (§2) — .env is gitignored, NEVER commit
32	
33	# No separate migrate step — every process (api/scheduler/ingest/mcp) calls the idempotent
    # ensure_schema() (db.base.ensure_schema, Base.metadata.create_all) at its own startup;
    # there is no Alembic migration chain in this project (§5 below).
34	uv run python -m ingest.backfill_cli --yes --market tw   # seed a little market data into DuckDB/Parquet (dev)
35	
36	uv run uvicorn api.app:app --reload          # the REST + SSE API  → http://localhost:8000
37	uv run python -m mcp                         # (separate shell) the MCP server
38	```
39	
40	Smoke-test a non-AI public read (no key needed):
41	
42	```bash
43	curl 'http://localhost:8000/api/v1/health'                 # source-health matrix
44	curl 'http://localhost:8000/api/v1/quote?symbols=2330&market=TW'   # EOD quote (public)
45	```
46	
47	An AI turn (needs a provider key in `.env`, §2) — streamed SSE:
48	
49	```bash
50	curl -N -X POST 'http://localhost:8000/api/v1/agent/query' \
51	  -H 'content-type: application/json' \
52	  -d '{"message":"台積電最近的籌碼？"}'
53	```
54	
55	**Local-parity alternative — `docker-compose`** ([modules/17](modules/17-devops-operations.md) §2)
56	brings up the full stack (api + scheduler-leader + worker + Redis + Postgres + DuckDB/Parquet
57	volume), so the Redis-transport + one-writer-DuckDB paths are exercised locally, not first
58	discovered in prod:
59	
60	```bash
61	docker compose up            # full stack on Postgres+Redis (prod-like)
62	docker compose --profile ollama up   # + local Ollama (offline LLM path)
63	```
64	
65	Use SQLite + `uv run` for fast iteration; use compose before merging anything touching Redis
66	transport, the market-data store, or concurrency ([modules/16](modules/16-infrastructure.md) §10).
67	
68	---
69	
70	## 2. Local infrastructure & secrets
71	
72	| Piece | Dev | Prod | Notes |
73	|---|---|---|---|
74	| **App DB** (users, threads, memory, usage, watchlists…) | **SQLite** file | **Postgres** | one schema, dual-dialect ([database-schema.md](database-schema.md)); `PRAGMA foreign_keys=ON` per SQLite conn or cascades no-op |
75	| **Market-data store** (EOD/history, scores, ETF holdings) | **DuckDB/Parquet** dir | same + more Parquet | leader writes, workers read-only Parquet ([modules/16](modules/16-infrastructure.md) §3) — **never** open the writer's `.duckdb` from a worker |
76	| **Event transport + cache** | Redis (compose) or in-proc (single-proc dev) | Redis | SSE fan-out, single-flight, quotas, OAuth-flow state ([modules/16](modules/16-infrastructure.md) §2/§3a) |
77	
78	**Secrets — [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §8.** `.env` is **gitignored** and
79	blocked from writes by the repo's PreToolUse hook. **Never read `.env` values aloud, echo, log, or
80	commit them.** Env var *names* (values live only in `.env`; see [modules/16](modules/16-infrastructure.md) §8):
81	
82	- **LLM (≥1 to run an AI turn):** the provider keys per [modules/04](modules/04-llm-adapter.md)
83	  (Anthropic/OpenAI/Gemini/Groq/Cerebras/OpenRouter; Ollama = keyless local). Provider chosen by
84	  config/env, never hardcoded.
85	- **Data:** `FINMIND_API_KEY` (only if exercising `sponsor` datasets locally — public reads work
86	  without it), `FINNHUB_KEY`, `BRAVE_SEARCH_API_KEY`. `ALPHA_VANTAGE_API_KEY`/`FRED_KEY`/`FMP_API_KEY`
87	  are **reserved for phased US/macro adapters** — not wired yet.
88	- **Auth/billing:** `JWT_SECRET_KEY`, Google/LINE OAuth creds, `PADDLE_*` (staging = Paddle sandbox).
89	- **Channels:** `TELEGRAM_BOT_TOKEN`, LINE messaging creds (distinct from LINE Login).
90	
91	**No startup network calls in prod** ([modules/17](modules/17-devops-operations.md)):
92	`INGEST_ON_STARTUP` is **dev-only**; prod boots clean and the leader scheduler owns ingest.
93	
94	---
95	
96	## 3. Build order (turn the blueprint into code)
97	
98	Follow the phase sequence in [architecture.md §11](architecture.md#11-build-phases) — each phase
99	links its module spec; build in order because each layer depends only on lower ones
100	([`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §3 **DEPENDENCY RULE**:
101	`interfaces → product → interaction → runtime → tools → context → dataaccess → sources`).
102	
103	| Phase | Build | Spec |
104	|---|---|---|
105	| 1 | Foundations — scaffold `backend/src/`, `config/`, `schemas/`, `db/`, TLS + enums, `pyproject.toml` | [architecture.md §9](architecture.md#9-repository-layout) |
106	| 2 | Data layer — the 14 source adapters + **data-access router** (capabilities, Market/Symbol, priority-fallback config) + contracts + **recorded fixtures** | [01](modules/01-sources.md), [19](modules/19-data-access-router.md) |
107	| 3 | Context pipeline (canonical/observation/conversation/source_health/builder) + calendar | [02](modules/02-packages.md), [18](modules/18-calendar-events.md) |
108	| 3a | **Ingest/ETL** — cron job groups, whole-market-by-date writers, idempotent upsert + gap-fill, backfill (§9c) | [22](modules/22-data-ingest.md) |
109	| 3b | **Analytics** — indicator library, multi-factor 多空 score, AI summary/SWOT, screener + top-N, themes | [20](modules/20-analytics-scoring.md) |
110	| 4 | Tool registry — tiered read-only tools (immediate core + deferred index) | [03](modules/03-tools.md) |
111	| 5 | LLM adapter — providers + registry + role-based routing | [04](modules/04-llm-adapter.md) |
112	| 6 | Agent runtime — loop, planner, context_window, **guardrails**, journal | [05](modules/05-agent-runtime.md) |
113	| 7 | Interaction layer — event bus + hooks, conversation store + SSE, memory, skills, feedback | [07](modules/07-conversation-streaming.md)–[10](modules/10-events-hooks.md), [13](modules/13-chat-interaction-features.md) |
114	| 8 | Orchestration — intent classifier → none/single/subagent/team, synthesis | [11](modules/11-orchestration.md) |
115	| 9 | Product layer — accounts/tiers/entitlements/BYOK, Paddle billing, chat features, voice, cosmetic pets, personality presets | [12](modules/12-multi-user-tiers.md), [15](modules/15-payments-paddle.md), [21](modules/21-pet-gamification.md), [23](modules/23-agent-personality.md) |
116	| 10 | Scheduler & alerts — HA worker + leader election, job/alert store, at-least-once delivery (§9a) | [14](modules/14-scheduler-alerts.md) |
117	| 11 | Interfaces — FastAPI routers, then MCP/FastMCP server + principal/entitlement | [06](modules/06-interfaces.md) |
118	| 12 | Infra & ops — Redis transport + cache, DuckDB/Parquet tier, `create_all()`-based dual-dialect schema provisioning, observability, deploy | [16](modules/16-infrastructure.md), [17](modules/17-devops-operations.md) |
119	| 13 | Hardening — test profiles + governance/isolation/context-efficiency/summary-quality/routing evals | §5 below |
120	
121	**Post-Phase-2 data-layer expansion:** FinMind/yfinance were widened well past their original
Phase-2 scope (8→104 FinMind datasets; yfinance 3 TW fields→full non-TW primary source) and MOPS
landed via TWSE OpenAPI. **Tool expansion** (7→107 tools against the newly-available datasets, incl.
the theme/score/screener/ETF-overlap/TDCC groups) and **doc sync** (`03-tools.md`, `data-catalog.md`,
`08-skills.md`, and other module docs corrected against real code) have both since landed. Most of
the long tail now has a skill allowlist home too (see the "Catch-all skills" table in
[08](modules/08-skills.md)). Remaining backlog, not yet closed: ingest job coverage (10 TW
whole-market FinMind jobs + 3 US-market jobs wired, real daemon-fired via `ingest/daemon.py` —
see [22](modules/22-data-ingest.md) for the full job inventory); `analytics/` is TW-only and still
missing its `summary/` subpackage (its `screener/`/`themes/`/`scoring/` subpackages now exist —
see Phase 3b); `calendar/rules.py` ignores
its own `market` parameter (TW logic runs regardless of market) — see [22](modules/22-data-ingest.md),
[20](modules/20-analytics-scoring.md), [18](modules/18-calendar-events.md) for verified detail.

**The vertical-slice alternative:** if you'd rather ship an end-to-end path early, build a thin
122	slice through phases 2→4→5→6→11 (one public capability → one tool → LLM → loop → `/agent/query`)
123	with everything else stubbed, then widen. Either way the **dependency rule + governance hooks are
124	not deferrable** — they go in from the first slice.
125	
126	---
127	
128	## 4. The two commandments a change must honor
129	
130	Everything in [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §3–§5 applies; these two cause the
131	most rework if missed:
132	
133	1. **Only `sources/` does network I/O** ([adr/0002](adr/0002-tool-mediated-io.md)). Callers request a
134	   **capability + (symbol, market)** from the data-access router — never a source id, never an HTTP
135	   client outside `sources/`. The Redis/DuckDB cache is a caching adapter *inside* `sources/`.
136	2. **Governance + entitlement are mandatory, non-skippable hooks** ([adr/0018](adr/0018-enforcement-seam-and-store-protocols.md),
137	   [modules/10](modules/10-events-hooks.md) §2). `guardrails.check_output` blocks buy/sell/target/
138	   sizing on **every** sub-agent output *and* final synthesis; `data_class` is resolved **per serving
139	   adapter** and gated **per tool call** (mid-turn tier change is safe). A persona/pet/skill layer may
140	   style tone only — it can never drop a caveat or emit a signal.
141	
142	---
143	
144	## 5. Testing runbook
145	
146	Non-network by default ([`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §7); CI's default suite
147	hits **no** upstream. Profiles are pytest markers:
148	
149	```bash
150	uv run pytest                          # default = -m 'not network' (CI default; recorded fixtures)
151	uv run pytest -m governance            # HIGHEST priority — the guardrail/entitlement suite (§ below)
152	uv run pytest -m concurrency           # shared-state races (needs Postgres+Redis) — module 16 §10
153	uv run pytest -m dual_dialect          # Base.metadata.create_all() produces a valid schema on BOTH SQLite and Postgres
154	uv run pytest -m operator_preflight    # live source fetch — MANUAL, opt-in only
155	uv run pytest -m browser_e2e           # scraper live path — MANUAL
156	
157	uv run ruff check --fix . && uv run ruff format .   # lint + format
158	uv run pyright && uv run mypy src                   # types (both must pass on new code)
159	uv run pre-commit run -a                            # ruff + pyright + governance-fast subset
160	```
161	
162	**The `governance` suite is the make-or-break** — it is tested directly against
163	[governance.md](governance.md): non-trading block, full-market-scan reject, caveat presence, sponsor
164	masking (public vs sponsor per serving adapter), canonical immutability, TLS no-silent-downgrade,
165	per-user isolation (no cross-`user_id` reads), prompt-injection resistance. Phase-13 also adds
166	context-efficiency, summary-quality, and classifier-routing evals ([architecture.md §11](architecture.md#11-build-phases)).
167	
168	**Write tests as you build:** recorded fixtures per source adapter (live fetch only under
    `operator_preflight`/`browser_e2e`); a **stub `LLMClient`** (deterministic scripted tool-calls) for
    all runtime/orchestrator tests — never a real provider in the default suite.

    **No Alembic migration chain.** This project has no `alembic.ini`/`versions/` directory —
    `Base.metadata.create_all()` (`db/base.py::ensure_schema`) is the **sole** schema-provisioning
    mechanism, called idempotently by every schema-bearing process (api/scheduler/ingest/mcp) at
    its own startup — there is no separate migrate step. Right after `create_all()` it runs
    `db/additive_upgrade.py`, which **adds** any column / index / named unique key an *existing*
    table is missing (so a database created by an older model version catches up without losing
    data); it never drops, renames or rewrites anything, and does nothing on a current database.
    Dropping a column, changing a type or a CHECK definition, and backfills stay a deliberate manual
    step. The `dual_dialect` marker above tests
    `create_all()`'s portability/idempotency across SQLite and Postgres, **not** migrations (see
    `tests/db/test_dual_dialect_migrations.py`'s own docstring).
171	
172	---
173	
174	## 6. "How to add X" recipes
175	
176	Each is a thin change *because* the architecture is seam-based. Follow the owning spec; the recipe is
177	the checklist.
178	
179	**Add a data source** → [modules/01](modules/01-sources.md), [adr/0002](adr/0002-tool-mediated-io.md)
180	1. New adapter in `sources/` implementing `SourceAdapter` (`fetch → validate → typed model`), tagging
181	   results with `source_id`, `timing_class`, `data_class`, `fetched_at`, `currentness_caveat`.
182	2. Declare its `supports() → {(capability, market)}`.
183	3. Add its id to the relevant `(market, capability)` lists in `config/source_priority.yaml`
184	   ([modules/19](modules/19-data-access-router.md) §4). **No caller code changes.**
185	4. Recorded fixture + adapter test. TLS via `sources/tls.py` (strict default, no silent downgrade).
186	
187	**Add a tool** → [modules/03](modules/03-tools.md)
188	1. Implement the `Tool` protocol (`name`, JSON input schema, `run()`), wrapping the router/context —
189	   never touching network/files directly.
190	2. Register it; decide **immediate** (always-sent core — the canonical set is defined once in
191	   [modules/03](modules/03-tools.md)) vs **deferred** (long-tail, loaded on demand). Default deferred.
192	3. If it serves `sponsor` data, it returns the masked teaser envelope for unentitled users — the
193	   `pre_tool` entitlement hook handles it; don't hand-roll the gate.
194	
195	**Add a skill (analysis playbook)** → [modules/08](modules/08-skills.md)
196	1. A `SKILL.md` (our frontmatter: name/version/triggers/tools/output_structure/freshness_rule/
197	   boundaries) in `skills/`. Its `tools` allowlist pre-loads exactly its deferred tools.
198	2. Guardrails still apply to its output — a trigger like 該買嗎 must reframe to descriptive factor
199	   context, never emit a signal.
200	
201	**Add a capability / a new market** → [modules/19](modules/19-data-access-router.md), [adr/0020](adr/0020-multi-market-data-access.md)
202	- New **capability**: define it, then list `(market → ordered adapters)` in `source_priority.yaml`;
203	  adapters declare support. Cache keys off `(capability, market, symbol[, date])` automatically.
204	- New **market** (JP/GB/…): a `MarketProfile` + a priority block + a calendar rule-set — **zero caller
205	  changes** (this is why the router exists).
206	
207	**Add an API endpoint** → [modules/06](modules/06-interfaces.md), [api-reference.md](api-reference.md)
208	- Add the router under `api/routers/`; **api-reference.md is the authoritative endpoint list** — keep
209	  it in sync. If it implies a new table, add it to [database-schema.md](database-schema.md) (dual-
210	  dialect: text UUID PK, JSON not `text[]`, CHECK not ENUM, money as int micro-units) — declare
    the new model on `Base` and it's picked up automatically by `create_all()`, no separate
    migration to write.
212	
213	**Add a DB table/column** → [database-schema.md](database-schema.md), [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §6
214	- Every stateful table carries `user_id` (isolation + RLS). Add the concurrency guard its hot path
215	  needs ([modules/16](modules/16-infrastructure.md) §10 matrix: partial-unique, `version` CAS,
216	  idempotency key, reserve-before-act…). The new/changed model must still pass `-m dual_dialect`
    (`create_all()` must produce a valid schema on both SQLite and Postgres — no migration to run).
217	
218	---
219	
220	## 7. The governance gate (before every merge)
221	
222	- **Run the domain gate:** the **`governance-reviewer`** agent (`.claude/agents/`) reviews a diff
223	  under `backend/src/` against [governance.md](governance.md) + [architecture.md](architecture.md) +
224	  [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md) §3–§5 — the check a generic code review misses.
225	- **CI must be green:** default `pytest` + `governance` + (if schema touched) `dual_dialect` +
226	  (if shared state touched) `concurrency`; ruff + pyright + mypy clean.
227	- **Schema-change safety:** there is no Alembic migration chain — `create_all()`
  (`db/base.py::ensure_schema`, plus the additive `db/additive_upgrade.py` step for existing
  tables) is the sole schema-provisioning mechanism, applied idempotently at
  each process's own startup. Additive/backward-compatible changes only (add columns/tables,
  never a breaking in-place rewrite of an existing one), since there is no expand/contract
228	  migration step to sequence a breaking change behind ([modules/17](modules/17-devops-operations.md) §1).
229	- **Branch, don't push to default; commit/push only when asked.** A change that weakens a
230	  non-negotiable to pass is a **reject**, not a merge — fix the code.
231	
232	---
233	
234	## 8. Where things live (quick map)
235	
236	Full layout: [architecture.md §9](architecture.md#9-repository-layout). Most-touched dirs:
237	
238	`sources/` (I/O only) · `dataaccess/` (capability router) · `context/` (M5F/K/N/Q packages) ·
239	`ingest/` + `analytics/` (derived store) · `tools/` · `llm/` · `agent/` (loop + guardrails) ·
240	`orchestrator/` · `conversation/` + `events/` + `memory/` + `skills/` (interaction) ·
241	`accounts/` + `payments/` + `usage/` + `scheduler/` + `voice/` + `personality/` + `pets/` (product) ·
242	`db/` · `api/` + `mcp/` (interfaces) · `config/` · `schemas/` · `tests/`.
243	
244	## Cross-references
245	
246	- Reading order + module map: [README.md](README.md)
247	- What & why: [architecture.md](architecture.md) · [governance.md](governance.md) · [adr/](adr/)
248	- Contracts: [api-reference.md](api-reference.md) · [database-schema.md](database-schema.md) · [data-catalog.md](data-catalog.md)
249	- Coding standards (agent-facing): [`../../backend/CLAUDE.md`](../../backend/CLAUDE.md)
250	