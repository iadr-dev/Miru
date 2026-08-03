# Module: DevOps & operations (delivery + run lifecycle)

Module 16 defines the **architecture** (what runs, how pieces talk). This module defines the
**delivery and run lifecycle** — CI/CD, packaging, environments, probes, shutdown, DR,
residency, cost ops, and runbooks. From the DevOps review (see [../adr/0016](../adr/0016-devops-operations.md)).

## 1. CI/CD pipeline

Python/FastAPI monorepo. Gates, in blocking order:

- **PR gate (fast, blocking):** ruff + black → mypy (strict) → `fast` unit → `default-ci`
  (non-network, fixtures) → **governance suite** (non-trading, caveat, per-user isolation,
  `sponsor` gate) → **dual-dialect Alembic test** (apply **+ rollback** on both SQLite and a
  Postgres service container) → secret scan (gitleaks — 35+ key env vars) + dep vuln
  (`pip-audit`) + image scan (Trivy) + SBOM.
- **Merge / nightly:** `full-non-network` + coverage.
- **Scheduled / pre-release (network, gated, low-quota tokens):** `operator-preflight` (live
  source reachability) + `browser-e2e` — hit FinMind/Brave/scraper quotas, so cron only, never
  per-PR.
- **Scraper contract canary (cron):** run versioned parsers against recorded + a small live
  sample and diff → catches MOPS/PTT/ETF-PCF/Brave drift before users do (operationalizes
  [16](16-infrastructure.md) §7 monitoring).
- **Deploy:** blue-green or canary; **migration order = expand → deploy → contract-later**
  (never a breaking migration in-place). Rollback = redeploy previous image + (if needed)
  down-migration; expand-contract keeps rollback safe.

## 2. Containerization & runtime

- **One image, role-selected entrypoint** (`api` | `scheduler` | `worker` via env) — parity
  across the three process types ([16](16-infrastructure.md) §1). Non-root, pinned base,
  `.dockerignore` excludes fixtures/secrets.
- **`docker-compose` (dev parity)** brings up the **full** stack: api + scheduler(leader) +
  worker + Redis + Postgres + Parquet/DuckDB volume + broker — so the Redis-transport path is
  exercised locally, not first discovered in prod.
- **Ollama** = optional compose profile (large/GPU); the offline/self-host path, **not** in the
  hosted prod image.

## 3. Environments

- **dev / staging / prod** config matrix. Staging uses **Paddle sandbox** (`PADDLE_ENV`) and a
  **separate FinMind token** (must not consume prod's pooled 6000/hr).
- FinMind has no sandbox → staging runs mostly on fixtures (non-network profiles) + a tiny live
  token. Staging **seed data** (accounts/tiers/threads) exercises entitlement gating + a
  Paddle-sandbox webhook-replay procedure.

## 4. Probes & lifecycle

- **`/livez`** (process up), **`/readyz`** (Redis + Postgres + broker reachable — refuses LB
  traffic if not), **`/startupz`**. These are **distinct from `/health`** (which is business
  data-currency + FinMind quota, not an infra probe). Scheduler/worker have their own liveness
  (leader-lock held? queue consumable?).
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
- **Log redaction:** no raw payloads / secrets / PII in log sinks or shipping — a startup
  self-check refuses to boot if the mandatory guardrail hooks aren't registered.
- **Audit-log shipping:** `journal_entries` is the audit record — define retention (see
  [../database-schema.md](../database-schema.md)), immutability/tamper-evidence, and off-box shipping.
- **GDPR/PDPA account deletion** flow: purge/anonymize across all `user_id`-scoped tables.

## 8. Runbooks, on-call, feature flags

- **Runbooks:** scraper breakage (detect drift → pin last-good parser → serve M5Q-degraded →
  patch versioned parser → canary → redeploy); Redis failover; FinMind pool saturation; provider
  outage / fallback-chain exhaustion; **Paddle webhook outage** (entitlement is webhook-sourced →
  backlog delays upgrades → replay/reconcile against Paddle).
- **On-call / incident severities** defined; alerting routes to the channel.
- **Feature flags** (config-driven, hot-reload where safe): dark-launch scrapers, toggle
  `AITW_WEBSEARCH` backend, gate new skills, **kill a misbehaving source without a deploy**.
- **MCP deployment surface:** separate process/port vs same image role, principal auth
  (API key / anonymous — [06](06-interfaces.md)), and rate-limiting like the REST tier — an
  externally-exposed surface, spec its exposure explicitly.

## Governance obligations
- Nothing here relaxes governance; it operationalizes it (log redaction, PII residency,
  non-trading self-check, audit shipping).
