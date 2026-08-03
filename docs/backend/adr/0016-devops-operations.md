# ADR 0016 — DevOps & operations: CI/CD, packaging, DR, residency, runbooks

## Status
Accepted. Adds [modules/17](../modules/17-devops-operations.md); complements the architecture
in [modules/16](../modules/16-infrastructure.md) / [adr/0015](0015-hosted-infrastructure.md).

## Context
The DevOps review found module 16 to be a strong *architecture* spec but the *delivery/run
lifecycle* undocumented: no CI/CD, containerization, environment model, probes, graceful SSE
shutdown, backup/DR, data-residency, cost-ops, or runbooks. It also surfaced two holes ADR 0015
introduced: **Redis is now a critical-path SPOF** (no HA/degradation), and the **broker/queue
backend + HA is unnamed**.

## Decision
Add an operations module capturing:
1. **CI/CD:** gated pipeline (lint/type/governance/dual-dialect-migration/security scans),
   non-network-by-default test matrix, scraper-contract canary, blue-green/canary deploy with
   expand→deploy→contract migration ordering + rollback.
2. **Packaging:** one role-selected image; compose brings up the full stack for dev parity;
   Ollama as an optional non-prod profile.
3. **Environments:** dev/staging/prod matrix; staging on Paddle sandbox + a separate FinMind token.
4. **Probes & lifecycle:** `/livez`/`/readyz`/`/startupz` distinct from data-`/health`; graceful
   SSE draining on SIGTERM with resume-vs-restart + cost-reconciliation defined.
5. **DR:** Postgres PITR + restore drill; **Redis HA + degradation tiers** (fail-closed quota,
   Postgres-advisory-lock fallback, `stream_events` DB-poll fallback) + per-keyspace persistence;
   DuckDB/Parquet rebuild runbook.
6. **Cost ops:** infra/vendor spend dashboards + budget alerts (complements app `usage_events`).
7. **Security/compliance ops:** ingress TLS/HSTS, PDPA data residency + PII-never-to-LLM
   assertion + sub-processor inventory, log redaction + boot-time guardrail self-check, audit-log
   retention/shipping, account-deletion flow.
8. **Runbooks/on-call/feature flags**, and the **MCP deployment surface** spec.
9. **Broker:** name the queue backend + HA (Decision deferred to build, but must be chosen; if
   Redis-backed, it inherits the Redis-HA requirement).

## Consequences
- Closes the day-2 surface; the blueprint moves from ~60% to build-ready on ops.
- Redis and the broker gain explicit HA/degradation requirements (were implicit SPOFs).
- Adds an operations/deployment build phase (architecture.md §11) before final hardening.
- No governance change — operationalizes existing rules (redaction, residency, non-trading).
