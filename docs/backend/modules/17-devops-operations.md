# Module: DevOps & operations (delivery + run lifecycle)

Module 16 defines the **architecture** (what runs, how pieces talk). This module defines the
**delivery and run lifecycle** — CI/CD, packaging, environments, probes, shutdown, DR,
residency, cost ops, and runbooks.

## 1. CI/CD pipeline

Python/FastAPI monorepo. There are **5 workflow files** in `.github/workflows/`:
`backend-ci.yml`, `frontend-ci.yml`, `secret-scan.yml`, `dependency-audit.yml` and
`coverage.yml`. Only the last two carry a `schedule:` trigger (weekly pip-audit and weekly
coverage floor); there is **no nightly/merge gate and no `operator-preflight`/`browser-e2e` cron
or scraper-contract canary** — those markers are manual.

- **PR gate, job 1 (`lint-type-test`):** ruff check → ruff format check (ruff is the sole
  lint+format tool — there is no black in this repo) → `mypy --strict` (second opinion) →
  `pyright --strict` (the primary type checker per [backend/CLAUDE.md](../../../backend/CLAUDE.md)
  §1) → **governance suite** (`pytest -m governance` — non-trading, caveat,
  per-user isolation, `sponsor` gate) → the default suite (`pytest -m "not network and not
  governance"`; `backend/pyproject.toml`'s `addopts` already scopes to `-m 'not network'`, and
  there is **no `fast`/`default-ci` marker split**).
- **PR gate, job 2 (`dual-dialect-migrations`):** runs against a real Postgres service
  container. This project has **no Alembic migration chain** (no `alembic.ini`, no
  `alembic/versions/`) — schema provisioning is `Base.metadata.create_all()` via
  `db/base.py::ensure_schema`, called once per schema-bearing process (api/scheduler/ingest/mcp)
  at its own startup. The `dual_dialect` marker (`pytest -m dual_dialect`) asserts that
  `create_all()` produces a complete, valid schema on **both** SQLite and Postgres — the real
  risk it catches is a partial/`WHERE`-qualified index or constraint that works on one dialect
  and silently doesn't exist on the other, not a migration apply/rollback (there is no migration
  to roll back). The same job also runs the `concurrency` suite (`pytest -m concurrency`,
  shared-state race/lost-update/TOCTOU tests) against the same container.
- **Secret scan:** `secret-scan.yml` runs **gitleaks** on every PR and push to `main`,
  deliberately not path-filtered (a leaked credential can land anywhere in the repo, not just
  `backend/`). `dependency-audit.yml` runs **pip-audit** on the locked
  dependencies (on lockfile changes and weekly). There is **no Trivy image scan and no SBOM
  step** in CI.
- **Deploy:** blue-green or canary. With no migration chain, a schema change ships via
  `create_all()`'s additive, idempotent behavior (new tables/columns appear; it never drops or
  alters existing ones) rather than an expand/contract migration sequence. Rollback = redeploy
  the previous image.

## 2. Containerization & runtime

- **One image, role-selected entrypoint** (`SERVICE_ROLE` = `api` | `scheduler` | `ingest` |
  `mcp`, see `backend/docker-entrypoint.sh`) — parity across the process types
  ([16](16-infrastructure.md) §1). Non-root (`USER app`), pinned `python:3.13-slim` base.
- **`docker-compose` (dev parity)** (`backend/docker-compose.yml`) brings up the **full** stack: api + scheduler +
  ingest + mcp + Redis + Postgres + a market-data volume — so the Redis-transport path is
  exercised locally, not first discovered in prod.
- **Local model** = served by Docker Model Runner outside compose (`LOCAL_LLM_BASE_URL`); the
  offline/self-host path, **not** in the hosted prod image.

## 3. Environments

- **dev / staging / prod** config matrix. Staging uses **Paddle sandbox** (`PADDLE_ENVIRONMENT`) and a
  **separate FinMind token** (must not consume prod's pooled 6000/hr).
- FinMind has no sandbox → staging runs mostly on fixtures (non-network profiles) + a tiny live
  token. Staging **seed data** (accounts/tiers/threads) exercises entitlement gating + a
  Paddle-sandbox webhook-replay procedure.

## 4. Probes & lifecycle

- **`/livez`** (process up), **`/readyz`** (Postgres, plus Redis when the event-bus or rate-limiter
  backend is `redis` — 503 if not), **`/startupz`** (same checks), in `api/routers/probes.py`. These are **distinct from `/health`** (which is business
  data-currency + FinMind quota, not an infra probe). Scheduler/worker have their own liveness
  (leader-lock held? queue consumable?).
- **Graceful shutdown / SSE draining:** on SIGTERM → stop accepting new turns → checkpoint or
  emit `turn.end` for in-flight turns → hold `terminationGracePeriodSeconds` (sized to P99 turn
  or a hard cap) → LB deregistration delay + `preStop`. Define whether a killed turn resumes on
  another worker (via `stream_events` replay) or restarts, and how the per-turn cost meter
  reconciles so reconnect+resume isn't double-charged.

## 5. Reliability / DR

- **Postgres:** managed PITR + **tested restore drill**; defined RPO/RTO. Holds the durable
  record (users, threads, usage, billing, entitlements) — non-negotiable backup.
- **Redis HA + degradation tiers** (Redis is now critical-path — transport + counters + locks +
  cache): Sentinel/managed-cluster failover. On Redis loss: SSE falls back to `stream_events`
  DB polling; quota checks **fail-closed** to conservative per-tier limits; locks fall back to
  **Postgres advisory locks**. Per-keyspace persistence: cache keys ephemeral, but **quota
  counters + idempotency locks need AOF or a Postgres-authoritative fallback** so a flush can't
  cause double-billing/double-delivery.
- **DuckDB/Parquet:** reconstructible cache of upstream data → document rebuild runbook + time
  (a cold rebuild re-hits FinMind quota) rather than backup.
- **Graceful degradation** when FinMind / a provider / a scraper is down → M5Q reports it;
  circuit breakers ([16](16-infrastructure.md) §6) prevent cascade.

## 6. Cost ops (infra/vendor — complements app-level `usage_events`)

- LLM spend dashboards **per provider/role/tier** and **BYOK vs platform** (platform eats
  non-BYOK inference); budget alerts **before** the per-turn ceilings are the only backstop.
- FinMind pool utilization + a cost/capacity alert at the saturation point ([16](16-infrastructure.md) §4).
- Brave spend, cloud infra (Redis/Postgres/compute/egress), Paddle fees → budget-alert channel.
- These are monthly-bill risks a runaway loop or viral user triggers; the per-turn kill-switch
  protects a turn, not the bill.

## 7. Security & compliance ops

- **Ingress TLS** (M6D §5 is *outbound*): managed certs, TLS 1.2+ min, HSTS at the edge.
- **Data residency / PII (PDPA):** `users` holds birthday/phone/nationality → choose a TW/APAC
  region; **assert PII never enters LLM prompts/journal/logs** (see `backend/CLAUDE.md` §8, security); publish
  a **sub-processor inventory** (7 LLM vendors + Brave + FinMind + Paddle + OAuth + LINE/Telegram/
  email) + backup residency. Paddle (merchant of record) handles payment PII; profile PII is ours.
- **Log redaction:** no raw payloads / secrets / PII in log sinks or shipping — a startup
  self-check refuses to boot if the mandatory guardrail hooks aren't registered.
- **Audit-log shipping:** the per-Step journal (`agent/journal.py`) is currently one structured
  log line per event (structlog), not a DB table — define retention, immutability/tamper-evidence,
  and off-box shipping for that log sink. Persistent tables are the SQLAlchemy models in
  `backend/src/db/models/`.
- **GDPR/PDPA account deletion** flow: purge/anonymize across all `user_id`-scoped tables.

## 8. Runbooks, on-call, feature flags

- **Runbooks:** scraper breakage (detect drift → pin last-good parser → serve M5Q-degraded →
  patch versioned parser → canary → redeploy); Redis failover; FinMind pool saturation; provider
  outage / fallback-chain exhaustion; **Paddle webhook outage** (entitlement is webhook-sourced →
  backlog delays upgrades → replay/reconcile against Paddle).
- **On-call / incident severities** defined; alerting routes to the channel.
- **Feature flags** (config-driven, hot-reload where safe): dark-launch scrapers, toggle the
  web-search backend, gate new skills, **kill a misbehaving source without a deploy**.
- **MCP deployment surface:** separate process/port vs same image role, principal auth
  (API key / anonymous — [06](06-interfaces.md)), and rate-limiting like the REST tier — an
  externally-exposed surface, spec its exposure explicitly.

## Governance obligations
- Nothing here relaxes the governance rules ([backend/CLAUDE.md](../../../backend/CLAUDE.md) §4, §8; [AGENTS.md](../../../AGENTS.md)); it operationalizes them (log redaction, PII residency,
  non-trading self-check, audit shipping).
