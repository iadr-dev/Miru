# backend/ — Coding Standards

Design-blueprint → real Python **FastAPI** backend. The **spec lives in `../docs/backend/`** —
read `architecture.md`, `governance.md`, and `modules/16-infrastructure.md` §10 before touching
shared state. **Governance rules are non-negotiable and load-bearing — never relax them to make
code pass.** See the project-root [`../AGENTS.md`](../AGENTS.md) for shared guidance.

> Resolved defaults for the architect's flagged ambiguities (override if the team disagrees):
> pyright primary + mypy CI · structlog · line-length 100 · SQLAlchemy 2.0 **async** · DuckDB
> version pinned once the store is stood up.

## 1. Toolchain (2026 baseline)

- **Python 3.13** target (`requires-python = ">=3.13"`). Local is 3.14; pin CI to 3.13 until all
  C-ext deps (duckdb, pydantic-core, pandas/pyarrow) ship 3.14 wheels; add a 3.14 CI leg as
  *allowed-fail* to track readiness.
- **uv** — sole packaging / venv / lock tool. Commit `uv.lock`. No pip/poetry/conda.
- **ruff** — lint **and** format (replaces black + isort + flake8 + pyupgrade).
- **pyright (strict)** — primary type checker; `mypy --strict` runs in CI as a second opinion on
  `src/`. New code must pass both.
- **pytest** + `pytest-asyncio` + `pytest-cov`; profiles via markers (§7).
- **pre-commit** runs ruff (check+format), pyright, and the governance-fast subset.

### Commands
```bash
uv sync --all-extras            # create/refresh venv from uv.lock
uv run <cmd>                    # run inside the env (never activate manually in CI)
uv run ruff check --fix .       # lint + autofix
uv run ruff format .            # format
uv run pyright                  # strict type check
uv run mypy src                 # CI second opinion
uv run pytest -m "not network"  # default suite (§7)
uv run pre-commit run -a
```

### pyproject.toml — tool stanzas
```toml
[tool.ruff]
target-version = "py313"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E","F","W","I","N","UP","B","A","C4","SIM","TID","PTH","ASYNC","RUF","ANN","T20","S","PL"]
ignore = ["ANN401"]  # explicit Any ONLY where justified with a noqa + reason
[tool.ruff.lint.flake8-tidy-imports.banned-api]
# enforce the DEPENDENCY RULE (§3): a layer may not import an upper layer;
# sources/* may not be imported outside dataaccess/ or ingest/.
[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101","ANN","PLR2004"]   # asserts, loose typing, magic values OK in tests
[tool.ruff.lint.flake8-bandit]
# S-rules: no verify=False, no hardcoded secrets, no eval.

[tool.pyright]
pythonVersion = "3.13"
typeCheckingMode = "strict"
include = ["src"]
reportMissingTypeStubs = false
reportUnknownMemberType = "warning"   # pandas/duckdb dynamic edges

[tool.mypy]
python_version = "3.13"
strict = true
plugins = ["pydantic.mypy"]
disallow_any_explicit = true
warn_unreachable = true
[[tool.mypy.overrides]]
module = ["duckdb.*","yfinance.*","FinMind.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-ra --strict-markers -m 'not network'"
markers = [
  "governance: highest-priority guardrail/entitlement suite",
  "network: hits real upstreams (opt-in only)",
  "operator_preflight: live source fetch (manual)",
  "browser_e2e: scraper live path (manual)",
  "concurrency: shared-state / race tests (Postgres+Redis)",
  "dual_dialect: must pass on SQLite AND Postgres",
]
```

## 2. Type & style

- `from __future__ import annotations` at the top of every module.
- **Full type hints** on all public functions/methods/attrs (incl. `-> None`).
- **No `Any` escapes.** Isolate unavoidable 3rd-party edges (duckdb/yfinance) in the adapter with
  `# noqa: ANN401 — <reason>`. `Any` never crosses a layer seam.
- **Pydantic v2 everywhere** for models / contracts / tool I/O / config:
  ```python
  class SourceResult(BaseModel):
      model_config = ConfigDict(frozen=True, extra="forbid")  # canonical = immutable (§5 gov)
      source_id: str
      timing_class: TimingClass
      fetched_at: datetime
      currentness_caveat: str = Field(min_length=1)  # never empty — governance §1.2
  ```
  Use `model_config` (not class `Config`), `Field(...)`, `field_validator`/`model_validator`.
- **PEP 8, ruff-enforced** — don't hand-format; run `ruff format`.
- **Docstrings only where behavior is non-obvious** (governance edges, concurrency mechanism, a
  caveat rule). No signature-restating noise.
- **Structured logging via structlog** — bind `turn_id`/`agent_id`/`node_id`/`user_id` (infra §7).
  **Never bare `print`** (ruff `T20`). **Never log secrets or raw source payloads.**

## 3. Architecture invariants → code rules

**DEPENDENCY RULE (one direction only):**
`interfaces → product → interaction → runtime → tools → context → dataaccess → sources`
- Nothing lower imports anything higher. Enforced by ruff `TID` banned-imports **and** an
  architecture test (`tests/arch/test_layering.py`) that walks the import graph and fails on any
  upward edge. `analytics/`, `ingest/`, `calendar/` read the derived store; they do **not** import
  request-path layers.
- Callers name a **capability + (symbol, market)** — **never a source id**. A `sources/*` import
  outside `dataaccess/` / `ingest/` is a layering violation.
- **ONLY `sources/` performs network I/O.** No `httpx`/`requests`/`socket`/scraper/SDK network
  call anywhere else. `tests/arch/test_network_io.py` fails on a network-client import outside `sources/` unless the module is on its reviewed allow-list (Redis transport, LLM/voice vendors, billing, OAuth, delivery). The
  Redis/DuckDB cache is a **caching `SourceAdapter` wrapper inside `sources/`** — `context/` never
  opens a socket/Redis/DuckDB itself (infra §3).
- **Async end-to-end.** Source/tool/LLM I/O is `async`. **Blocking SDKs/scrapers** (FinMind,
  yfinance, HTML scrapers, some LLM SDKs) run in a threadpool (`anyio.to_thread.run_sync`) — never
  block the event loop / SSE fan-out (infra §6).
- **`Protocol`-based seams** — depend on `LLMClient`, `SourceAdapter`, `Tool`, `MarketDataStore`,
  `ConversationStore`, `MemoryStore` protocols, never a concrete class. Impl chosen by **config**.
- **No hardcoded provider/model/source** in core — all from `config/` + env.

## 4. Governance-as-code (non-negotiable)

- **Guardrails + entitlement are mandatory hooks** (`pre_tool`/`pre_output`) — cannot be skipped
  or config-disabled. `guardrails.py` owns governance; `accounts/entitlements.py` owns the
  `sponsor` gate — two separate hooks.
- **Non-trading enforced in `guardrails.check_output`:** block/redact buy/sell/hold, price
  targets, position sizing — on **every** sub-agent output AND final synthesis. Personality/pet/
  skill layers are appended **below** governance and also scanned — no persona drops a caveat or
  emits a signal (governance §8).
- **Caveats preserved end-to-end.** Every `SourceResult` carries `currentness_caveat`; compaction
  never summarizes away canonical facts/caveats. Uncited web claims + unattributed analyst targets
  are **blocked**.
- **No full-market scan** — reject tool args requesting the whole market; ingest is the *only*
  whole-market path (§9c, market-wide **by-date**, never per-symbol).
- **Secrets never logged/committed.** `.env` gitignored; provider/BYOK keys KMS-encrypted,
  decrypt in-process at call time only, never cross the sub-agent/scheduler boundary in plaintext,
  never in prompts/journal/memory/logs (governance §6/§9, infra §8).
- **DuckDB: leader-writes-Parquet / workers-read-only.** Leader ingest = **sole** DuckDB writer
  (Hive-partitioned Parquet `market=…/date=…`). API workers open **Parquet read-only** via
  per-worker `:memory:` DuckDB `read_parquet(glob, hive_partitioning=true)` — never the writer's
  `.duckdb` file. Pin the DuckDB version (infra §3/§10, ingest §7).
- **Atomic partition swap** — temp dir → `os.rename` → flip manifest pointer; readers resolve the
  manifest first. Sweep orphaned temp dirs on leader startup. **Bulk `COPY … TO parquet`, never
  row `INSERT`, never CSV staging** (ingest §1a/§4).
- **No startup network calls in prod.** `INGEST_ON_STARTUP` is dev-only; prod boots clean, ingest
  owned by the leader scheduler (ingest §6a).

## 5. Concurrency (infra §10 matrix is the contract)

Never rely on "one process at a time" (true only for dev SQLite; load-bearing on Postgres/Redis):
- **Atomic ops over read-modify-write:** counters `INSERT … ON CONFLICT (pk) DO UPDATE SET n=n+1`;
  rate/quota via Redis `INCR`+TTL in the *same* op as the check.
- **Reserve-before-act** for budgets/quotas (FinMind pool, gacha pulls): atomic decrement, refund
  on failure — avoids TOCTOU.
- **Single writer where ordering matters:** `event_seq`/`messages.seq` from the one event-bus
  writer per turn (`UNIQUE(turn_id, seq)`); `subscriptions` written only by the Paddle webhook
  with `occurred_at` order guard.
- **Idempotency keys** on every retryable mutation: turns `UNIQUE(user_id, idempotency_key)`,
  deliveries `dedupe_key`, webhooks `paddle_event_id`.
- **Optimistic `version` CAS** for multi-device edits (`threads`, `schedule_jobs`) → 409 on stale.
- **One active turn per thread:** partial `UNIQUE(thread_id) WHERE status='running'`.
- **Single-flight** on cache miss (per-key Redis lock) — protects the FinMind Sponsor pool.
- **Entitlement resolved per tool call** (in `pre_tool`), not cached for the turn.

## 6. DB portability (SQLite dev ↔ Postgres prod)

- **UUID PKs = app-generated `text`** on both. Don't rely on `gen_random_uuid()`.
- **JSON not JSONB** where portable; `jsonb` only for Postgres columns you query *into*. Arrays
  (`tags`) as **JSON array**, not PG `text[]`.
- **Money as integer micro-units** — SQLite `REAL` loses precision; no `Decimal` columns.
- **CHECK constraints, not native ENUM** for all constrained text (`tier`/`status`/`role`/`type`/
  `data_class`…).
- **Schema provisioning (development stage): no Alembic.** `Base.metadata.create_all()` plus
  `db/additive_upgrade.py` (adds missing columns/indexes/named uniques, never drops or rewrites) run
  at every process start. A destructive or data-dependent model change means rebuilding the dev
  database - keep the schema fresh and clean rather than carrying migration history. **Alembic is
  adopted when the first production version ships:** a baseline migration generated from the models,
  then forward-only migrations that apply on **both** SQLite and Postgres in CI (`dual_dialect`
  marker), expand-contract for message-tree / `stream_events` changes.
- Set `PRAGMA foreign_keys=ON` per SQLite connection or `ON DELETE CASCADE` silently no-ops.
- Per-user isolation: session-scoped store wrapper injects `WHERE user_id = ?`; Postgres RLS as
  prod backstop.

## 7. Testing

- **Non-network by default** (`-m 'not network'`) — CI default suite hits no upstream.
- **Recorded fixtures per source adapter**; live fetch only under `operator_preflight` /
  `browser_e2e`, run manually.
- **Stub `LLMClient`** (deterministic scripted responses) for all runtime/orchestrator tests.
- **Governance suite is highest priority** (`@pytest.mark.governance`): non-trading block,
  full-market-scan reject, caveat presence, sponsor masking, canonical immutability, TLS
  no-silent-downgrade, prompt-injection resistance. Tested directly against `governance.md`.
- **Profiles:** default · `governance` · `concurrency` (Postgres+Redis race/lost-update/TOCTOU) ·
  `dual_dialect` (migrations) · `operator_preflight`/`browser_e2e` (live, manual) · plus the
  phase-13 evals (per-user isolation, context-efficiency, summary-quality, classifier-routing).

## 8. Security

- **Credential settings are `SecretStr`** (`config/settings.py`): their repr is masked, so a printed or logged settings object cannot leak a key. Unwrap with `reveal(...)` / `.get_secret_value()` only at the call that needs the plain value; `tests/config/test_secret_settings.py` fails on a credential-looking setting typed as a plain `str`.
- **`.env` gitignored**; secrets via env at runtime, KMS envelope-encrypted at rest
  (`user_secrets.ciphertext` for BYOK). Decrypt in-process at call time only.
- **BYOK** keys encrypted at rest, read at call time; a removed key fails that one call gracefully
  (fallback), never a torn read. BYOK lifts **only** the LLM chat limit.
- **No secrets in prompts / journal / memory / logs.** The LLM adapter transmits only the Mode-C
  conversation package + governed context — never raw source payloads.
- **TLS strict-by-default (M6D)** in `sources/tls.py`: `strict | compatibility | unsafe-explicit`,
  chosen explicitly, no silent downgrade; non-strict is loudly logged. ruff `S` blocks
  `verify=False` outside that seam.
- Short-lived JWT access token (~15 min, in-memory); refresh token in `httpOnly; Secure;
  SameSite=Lax` cookie with rotation + reuse detection.
- Untrusted external content (`fetch_url`/`web_search`/news/PTT) is **data, never instructions** —
  schema-validated tool outputs are the boundary; `pre_output` non-trading scan is the backstop.
