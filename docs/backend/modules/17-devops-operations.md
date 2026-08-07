# Module: DevOps & operations (delivery + run lifecycle)

Module 16 defines the **architecture** (what runs, how pieces talk). This module defines the
**delivery and run lifecycle** — CI/CD, packaging, environments, probes, shutdown, DR,
residency, cost ops, and runbooks. From the DevOps review (see [../adr/0016](../adr/0016-devops-operations.md)).

## 1. CI/CD pipeline

Python/FastAPI monorepo. **Real CI (`.github/workflows/ci.yml`), in blocking order:**

- **`lint-type-test` job:** `ruff check` → `ruff format --check` (ruff is both linter and
  formatter — it replaces black; see `backend/CLAUDE.md`) → `mypy --strict` → `pyright` (strict)
  → the default non-network `pytest` suite → the **governance suite** (`pytest -m governance`).
- **`dual-dialect-migrations` job (separate, parallel):** applies the Alembic migration suite
  against **both SQLite and a real Postgres service container** (`pytest -m dual_dialect`), then
  the **concurrency suite** (`pytest -m concurrency`) against the same Postgres container
  (shared-state race/lost-update/TOCTOU proofs, [16](16-infrastructure.md) §10).
- **`secret-scan` job:** gitleaks.
- **Not yet built:** dependency-vulnerability scanning (`pip-audit`), container image scanning
  (Trivy), SBOM generation, a scheduled/pre-release `operator-preflight`/`browser-e2e` network
  job, a scraper-contract canary job, and any deploy-stage automation (blue-green/canary rollout,
  expand-deploy-contract migration ordering) — these remain design intent, not current pipeline
  steps. The CI trigger itself is PR + push-to-`main` only (no nightly/merge-queue job exists).

## 2. Containerization & runtime

- **One image, role-selected entrypoint** — real roles are **`api` | `scheduler` | `ingest` |
  `mcp`** (`docker-entrypoint.sh`'s `SERVICE_ROLE` dispatch; not `worker` — there is no generic
  queue-worker role in this codebase). Parity across all four process types
  ([16](16-infrastructure.md) §1, which itself needs correcting to the same four roles — no
  `worker(queue)` process exists yet, only `api`/`scheduler`/`ingest`/`mcp`). Non-root, pinned
  base (`python:3.13-slim`), no `.dockerignore`-driven secret exclusion beyond the standard
  build-context copy allowlist in the `Dockerfile`.
- **`docker-compose.yml` (dev parity)** brings up the real stack: `postgres` + `redis` +
  a one-shot `migrate` job (`alembic upgrade head`) + `api` + `scheduler` + `ingest` + `mcp` (each
  the same image, role-selected via `SERVICE_ROLE`) — so the Redis-transport path (`EVENT_BUS_
  BACKEND=redis`/`RATE_LIMITER_BACKEND=redis`) is exercised locally, not first discovered in
  prod. **No separate `worker`/broker/queue service exists** — that process type is design
  intent per [16](16-infrastructure.md) §1, not part of the real compose file today.
- **Ollama** = optional compose profile (`--profile ollama`, large/GPU); the offline/self-host
  path, **not** in the hosted prod image or brought up by default.

## 3. Environments

- **dev / staging / prod** config matrix. Staging uses **Paddle sandbox** (`PADDLE_ENVIRONMENT`) and a
  **separate FinMind token** (must not consume prod's pooled 6000/hr).
- FinMind has no sandbox → staging runs mostly on fixtures (non-network profiles) + a tiny live
  token. Staging **seed data** (accounts/tiers/threads) exercises entitlement gating + a
  Paddle-sandbox webhook-replay procedure.

## 4. Probes & lifecycle

- **`/livez`** (process up, no dependency check), **`/readyz`** (Postgres always, Redis only when
  `event_bus_backend`/`rate_limiter_backend` is configured `redis` — refuses LB traffic on a 503
  with a per-dependency breakdown if not), **`/startupz`** (same checks as `/readyz`, reported
  under a separate path so an orchestrator can tell "still booting" from "was ready, now
  degraded") — all real, in `api/routers/probes.py`, mounted unprefixed (not under `/api/v1`).
  These are **distinct from `/health`** (which is business data-currency + FinMind quota, not an
  infra probe). **Not yet implemented:** a broker-reachability check in `/readyz` (no broker/queue
  process exists yet — see [16](16-infrastructure.md) §1's `worker(queue)` process type) and
  scheduler/worker's own leader-lock/queue-consumable liveness probes.
- **Graceful shutdown / SSE draining:** on SIGTERM → stop accepting new turns → checkpoint or
  emit `turn.end` for in-flight turns → hold `terminationGracePeriodSeconds` (sized to P99 turn
  or a hard cap) → LB deregistration delay + `preStop`. Define whether a killed turn resumes on
  another worker (via `stream_events` replay) or restarts, and how the per-turn cost meter
  reconciles so reconnect+resume isn't double-charged.

## 5. Reliability / DR

- **Postgres:** managed PITR + **tested restore drill**; defined RPO/RTO. Holds the auditable
  record (governance §7/§9) — non-negotiable backup.
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
  region; **assert PII never enters LLM prompts/journal/logs** (extends governance §9); publish
  a **sub-processor inventory** (7 LLM vendors + Brave + FinMind + Paddle + OAuth + LINE/Telegram/
  email) + backup residency. Paddle (merchant of record) handles payment PII; profile PII is ours.
- **Log redaction:** no raw payloads / secrets / PII in log sinks or shipping (`structlog` usage
  throughout the codebase never binds a raw secret/token/body — see `backend/CLAUDE.md` §2/§8).
  **Not yet built:** a startup self-check that refuses to boot if the mandatory guardrail hooks
  aren't registered — no such check exists in `api/composition.py`/`mcp/composition.py` today.
- **Audit-log shipping:** `journal_entries` is the audit record — define retention (see
  [../database-schema.md](../database-schema.md)), immutability/tamper-evidence, and off-box shipping.
- **GDPR/PDPA account deletion** flow: purge/anonymize across all `user_id`-scoped tables — not
  yet built (no deletion/purge endpoint or job exists in `accounts/` today).

## 8. Runbooks, on-call, feature flags

- **Runbooks:** scraper breakage (detect drift → pin last-good parser → serve M5Q-degraded →
  patch versioned parser → canary → redeploy); Redis failover; FinMind pool saturation; provider
  outage / fallback-chain exhaustion; **Paddle webhook outage** (entitlement is webhook-sourced →
  backlog delays upgrades → replay/reconcile against Paddle).
- **On-call / incident severities** defined; alerting routes to the channel.
- **Feature flags — not yet built.** No config-driven, hot-reloadable feature-flag system exists
  (no `AITW_WEBSEARCH`-style env toggle, no dark-launch mechanism); the closest real analog is
  `accounts.entitlements.feature_allowed`'s static feature→min-tier table (gates a feature by
  the caller's tier, not a deploy-time on/off switch) and each source adapter's own
  presence-of-credential degrade (a provider with no key configured simply reports unhealthy —
  not the same as an operator flipping a flag to kill it live).
- **MCP deployment surface:** separate process/port vs same image role, principal auth
  (API key / anonymous — [06](06-interfaces.md)), and rate-limiting like the REST tier — an
  externally-exposed surface, spec its exposure explicitly.

## Governance obligations
- Nothing here relaxes governance; it operationalizes it (log redaction, PII residency,
  non-trading self-check, audit shipping).
