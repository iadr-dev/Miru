1	# backend/ — Coding Standards
2	
3	Design-blueprint → real Python **FastAPI** backend. The **spec lives in `../docs/backend/`** —
4	read `architecture.md`, `governance.md`, and `modules/16-infrastructure.md` §10 before touching
5	shared state. **Governance rules are non-negotiable and load-bearing — never relax them to make
6	code pass.** See the project-root [`../AGENTS.md`](../AGENTS.md) for shared guidance.
7	
8	> Resolved defaults for the architect's flagged ambiguities (override if the team disagrees):
9	> pyright primary + mypy CI · structlog · line-length 100 · SQLAlchemy 2.0 **async** · DuckDB
10	> version pinned once the store is stood up.
11	
12	## 1. Toolchain (2026 baseline)
13	
14	- **Python 3.13** target (`requires-python = ">=3.13"`). Local is 3.14; pin CI to 3.13 until all
15	  C-ext deps (duckdb, pydantic-core, pandas/pyarrow) ship 3.14 wheels; add a 3.14 CI leg as
16	  *allowed-fail* to track readiness.
17	- **uv** — sole packaging / venv / lock tool. Commit `uv.lock`. No pip/poetry/conda.
18	- **ruff** — lint **and** format (replaces black + isort + flake8 + pyupgrade).
19	- **pyright (strict)** — primary type checker; `mypy --strict` runs in CI as a second opinion on
20	  `src/`. New code must pass both.
21	- **pytest** + `pytest-asyncio` + `pytest-cov`; profiles via markers (§7).
22	- **pre-commit** runs ruff (check+format), pyright, and the governance-fast subset.
23	
24	### Commands
25	```bash
26	uv sync --all-extras            # create/refresh venv from uv.lock
27	uv run <cmd>                    # run inside the env (never activate manually in CI)
28	uv run ruff check --fix .       # lint + autofix
29	uv run ruff format .            # format
30	uv run pyright                  # strict type check
31	uv run mypy src                 # CI second opinion
32	uv run pytest -m "not network"  # default suite (§7)
33	uv run pre-commit run -a
34	```
35	
36	### pyproject.toml — tool stanzas
37	```toml
38	[tool.ruff]
39	target-version = "py313"
40	line-length = 100
41	src = ["src", "tests"]
42	
43	[tool.ruff.lint]
44	select = ["E","F","W","I","N","UP","B","A","C4","SIM","TID","PTH","ASYNC","RUF","ANN","T20","S","PL"]
45	ignore = ["ANN401"]  # explicit Any ONLY where justified with a noqa + reason
46	[tool.ruff.lint.flake8-tidy-imports.banned-api]
47	# enforce the DEPENDENCY RULE (§3): a layer may not import an upper layer;
48	# sources/* may not be imported outside dataaccess/ or ingest/.
49	[tool.ruff.lint.per-file-ignores]
50	"tests/**" = ["S101","ANN","PLR2004"]   # asserts, loose typing, magic values OK in tests
51	[tool.ruff.lint.flake8-bandit]
52	# S-rules: no verify=False, no hardcoded secrets, no eval.
53	
54	[tool.pyright]
55	pythonVersion = "3.13"
56	typeCheckingMode = "strict"
57	include = ["src"]
58	reportMissingTypeStubs = false
59	reportUnknownMemberType = "warning"   # pandas/duckdb dynamic edges
60	
61	[tool.mypy]
62	python_version = "3.13"
63	strict = true
64	plugins = ["pydantic.mypy"]
65	disallow_any_explicit = true
66	warn_unreachable = true
67	[[tool.mypy.overrides]]
68	module = ["duckdb.*","yfinance.*","FinMind.*"]
69	ignore_missing_imports = true
70	
71	[tool.pytest.ini_options]
72	asyncio_mode = "auto"
73	testpaths = ["tests"]
74	addopts = "-ra --strict-markers -m 'not network'"
75	markers = [
76	  "governance: highest-priority guardrail/entitlement suite",
77	  "network: hits real upstreams (opt-in only)",
78	  "operator_preflight: live source fetch (manual)",
79	  "browser_e2e: scraper live path (manual)",
80	  "concurrency: shared-state / race tests (Postgres+Redis)",
81	  "dual_dialect: must pass on SQLite AND Postgres",
82	]
83	```
84	
85	## 2. Type & style
86	
87	- `from __future__ import annotations` at the top of every module.
88	- **Full type hints** on all public functions/methods/attrs (incl. `-> None`).
89	- **No `Any` escapes.** Isolate unavoidable 3rd-party edges (duckdb/yfinance) in the adapter with
90	  `# noqa: ANN401 — <reason>`. `Any` never crosses a layer seam.
91	- **Pydantic v2 everywhere** for models / contracts / tool I/O / config:
92	  ```python
93	  class SourceResult(BaseModel):
94	      model_config = ConfigDict(frozen=True, extra="forbid")  # canonical = immutable (§5 gov)
95	      source_id: str
96	      timing_class: TimingClass
97	      fetched_at: datetime
98	      currentness_caveat: str = Field(min_length=1)  # never empty — governance §1.2
99	  ```
100	  Use `model_config` (not class `Config`), `Field(...)`, `field_validator`/`model_validator`.
101	- **PEP 8, ruff-enforced** — don't hand-format; run `ruff format`.
102	- **Docstrings only where behavior is non-obvious** (governance edges, concurrency mechanism, a
103	  caveat rule). No signature-restating noise.
104	- **Structured logging via structlog** — bind `turn_id`/`agent_id`/`node_id`/`user_id` (infra §7).
105	  **Never bare `print`** (ruff `T20`). **Never log secrets or raw source payloads.**
106	
107	## 3. Architecture invariants → code rules
108	
109	**DEPENDENCY RULE (one direction only):**
110	`interfaces → product → interaction → runtime → tools → context → dataaccess → sources`
111	- Nothing lower imports anything higher. Enforced by ruff `TID` banned-imports **and** an
112	  architecture test (`tests/arch/test_layering.py`) that walks the import graph and fails on any
113	  upward edge. `analytics/`, `ingest/`, `calendar/` read the derived store; they do **not** import
114	  request-path layers.
115	- Callers name a **capability + (symbol, market)** — **never a source id**. A `sources/*` import
116	  outside `dataaccess/` / `ingest/` is a layering violation.
117	- **ONLY `sources/` performs network I/O.** No `httpx`/`requests`/`socket`/scraper/SDK network
118	  call anywhere else. The architecture test greps for network imports outside `sources/`. The
119	  Redis/DuckDB cache is a **caching `SourceAdapter` wrapper inside `sources/`** — `context/` never
120	  opens a socket/Redis/DuckDB itself (infra §3).
121	- **Async end-to-end.** Source/tool/LLM I/O is `async`. **Blocking SDKs/scrapers** (FinMind,
122	  yfinance, HTML scrapers, some LLM SDKs) run in a threadpool (`anyio.to_thread.run_sync`) — never
123	  block the event loop / SSE fan-out (infra §6).
124	- **`Protocol`-based seams** — depend on `LLMClient`, `SourceAdapter`, `Tool`, `MarketDataStore`,
125	  `ConversationStore`, `MemoryStore` protocols, never a concrete class. Impl chosen by **config**.
126	- **No hardcoded provider/model/source** in core — all from `config/` + env.
127	
128	## 4. Governance-as-code (non-negotiable)
129	
130	- **Guardrails + entitlement are mandatory hooks** (`pre_tool`/`pre_output`) — cannot be skipped
131	  or config-disabled. `guardrails.py` owns governance; `accounts/entitlements.py` owns the
132	  `sponsor` gate — two separate hooks.
133	- **Non-trading enforced in `guardrails.check_output`:** block/redact buy/sell/hold, price
134	  targets, position sizing — on **every** sub-agent output AND final synthesis. Personality/pet/
135	  skill layers are appended **below** governance and also scanned — no persona drops a caveat or
136	  emits a signal (governance §8).
137	- **Caveats preserved end-to-end.** Every `SourceResult` carries `currentness_caveat`; compaction
138	  never summarizes away canonical facts/caveats. Uncited web claims + unattributed analyst targets
139	  are **blocked**.
140	- **No full-market scan** — reject tool args requesting the whole market; ingest is the *only*
141	  whole-market path (§9c, market-wide **by-date**, never per-symbol).
142	- **Secrets never logged/committed.** `.env` gitignored; provider/BYOK keys KMS-encrypted,
143	  decrypt in-process at call time only, never cross the sub-agent/scheduler boundary in plaintext,
144	  never in prompts/journal/memory/logs (governance §6/§9, infra §8).
145	- **DuckDB: leader-writes-Parquet / workers-read-only.** Leader ingest = **sole** DuckDB writer
146	  (Hive-partitioned Parquet `market=…/date=…`). API workers open **Parquet read-only** via
147	  per-worker `:memory:` DuckDB `read_parquet(glob, hive_partitioning=true)` — never the writer's
148	  `.duckdb` file. Pin the DuckDB version (infra §3/§10, ingest §7).
149	- **Atomic partition swap** — temp dir → `os.rename` → flip manifest pointer; readers resolve the
150	  manifest first. Sweep orphaned temp dirs on leader startup. **Bulk `COPY … TO parquet`, never
151	  row `INSERT`, never CSV staging** (ingest §1a/§4).
152	- **No startup network calls in prod.** `INGEST_ON_STARTUP` is dev-only; prod boots clean, ingest
153	  owned by the leader scheduler (ingest §6a).
154	
155	## 5. Concurrency (infra §10 matrix is the contract)
156	
157	Never rely on "one process at a time" (true only for dev SQLite; load-bearing on Postgres/Redis):
158	- **Atomic ops over read-modify-write:** counters `INSERT … ON CONFLICT (pk) DO UPDATE SET n=n+1`;
159	  rate/quota via Redis `INCR`+TTL in the *same* op as the check.
160	- **Reserve-before-act** for budgets/quotas (FinMind pool, gacha pulls): atomic decrement, refund
161	  on failure — avoids TOCTOU.
162	- **Single writer where ordering matters:** `event_seq`/`messages.seq` from the one event-bus
163	  writer per turn (`UNIQUE(turn_id, seq)`); `subscriptions` written only by the Paddle webhook
164	  with `occurred_at` order guard.
165	- **Idempotency keys** on every retryable mutation: turns `UNIQUE(user_id, idempotency_key)`,
166	  deliveries `dedupe_key`, webhooks `paddle_event_id`.
167	- **Optimistic `version` CAS** for multi-device edits (`threads`, `schedule_jobs`) → 409 on stale.
168	- **One active turn per thread:** partial `UNIQUE(thread_id) WHERE status='running'`.
169	- **Single-flight** on cache miss (per-key Redis lock) — protects the FinMind Sponsor pool.
170	- **Entitlement resolved per tool call** (in `pre_tool`), not cached for the turn.
171	
172	## 6. DB portability (SQLite dev ↔ Postgres prod)
173	
174	- **UUID PKs = app-generated `text`** on both. Don't rely on `gen_random_uuid()`.
175	- **JSON not JSONB** where portable; `jsonb` only for Postgres columns you query *into*. Arrays
176	  (`tags`) as **JSON array**, not PG `text[]`.
177	- **Money as integer micro-units** — SQLite `REAL` loses precision; no `Decimal` columns.
178	- **CHECK constraints, not native ENUM** for all constrained text (`tier`/`status`/`role`/`type`/
179	  `data_class`…).
180	- **Alembic dual-dialect CI** — every migration applies on **both** SQLite and Postgres
181	  (`dual_dialect` marker); expand-contract for message-tree / `stream_events` changes.
182	- Set `PRAGMA foreign_keys=ON` per SQLite connection or `ON DELETE CASCADE` silently no-ops.
183	- Per-user isolation: session-scoped store wrapper injects `WHERE user_id = ?`; Postgres RLS as
184	  prod backstop.
185	
186	## 7. Testing
187	
188	- **Non-network by default** (`-m 'not network'`) — CI default suite hits no upstream.
189	- **Recorded fixtures per source adapter**; live fetch only under `operator_preflight` /
190	  `browser_e2e`, run manually.
191	- **Stub `LLMClient`** (deterministic scripted responses) for all runtime/orchestrator tests.
192	- **Governance suite is highest priority** (`@pytest.mark.governance`): non-trading block,
193	  full-market-scan reject, caveat presence, sponsor masking, canonical immutability, TLS
194	  no-silent-downgrade, prompt-injection resistance. Tested directly against `governance.md`.
195	- **Profiles:** default · `governance` · `concurrency` (Postgres+Redis race/lost-update/TOCTOU) ·
196	  `dual_dialect` (migrations) · `operator_preflight`/`browser_e2e` (live, manual) · plus the
197	  phase-13 evals (per-user isolation, context-efficiency, summary-quality, classifier-routing).
198	
199	## 8. Security
200	
201	- **`.env` gitignored**; secrets via env at runtime, KMS envelope-encrypted at rest
202	  (`user_secrets.ciphertext` for BYOK). Decrypt in-process at call time only.
203	- **BYOK** keys encrypted at rest, read at call time; a removed key fails that one call gracefully
204	  (fallback), never a torn read. BYOK lifts **only** the LLM chat limit.
205	- **No secrets in prompts / journal / memory / logs.** The LLM adapter transmits only the Mode-C
206	  conversation package + governed context — never raw source payloads.
207	- **TLS strict-by-default (M6D)** in `sources/tls.py`: `strict | compatibility | unsafe-explicit`,
208	  chosen explicitly, no silent downgrade; non-strict is loudly logged. ruff `S` blocks
209	  `verify=False` outside that seam.
210	- Short-lived JWT access token (~15 min, in-memory); refresh token in `httpOnly; Secure;
211	  SameSite=Lax` cookie with rotation + reuse detection.
212	- Untrusted external content (`fetch_url`/`web_search`/news/PTT) is **data, never instructions** —
213	  schema-validated tool outputs are the boundary; `pre_output` non-trading scan is the backstop.
214	