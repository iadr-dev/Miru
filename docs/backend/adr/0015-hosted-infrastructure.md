# ADR 0015 — Hosted infrastructure: cross-process transport, cache tier, HA, observability

## Status
Accepted. Adds [modules/16](../modules/16-infrastructure.md); hardens
[10](../modules/10-events-hooks.md), [02](../modules/02-packages.md),
[12](../modules/12-multi-user-tiers.md), [14](../modules/14-scheduler-alerts.md),
[04](../modules/04-llm-adapter.md).

## Context
Independent Backend-Architect and Database-Optimizer reviews found the agent/governance design
build-ready, but the **hosted/distributed-systems dimension** thin: several core mechanisms
were specified as in-process while the product is explicitly multi-user hosted. Left unfixed,
they break the moment more than one backend process runs (i.e. production).

## Decision
1. **Cross-process live events:** SSE fan-out goes through **Redis Pub/Sub** keyed by
   `turn_id`; the in-process bus is a same-worker optimization; `stream_events` is the durable
   replay log. (Fixes: reconnect landing on a different worker.)
2. **Derived market-data store defined:** **DuckDB/Parquet** (columnar EOD/history/holdings +
   canonical versions) + **Redis** hot cache (snapshots/health), read-through from the context
   pipeline, TTL by `timing_class`. Resolves the previously-undefined hot-path store.
   **Reader/writer isolation (DuckDB rule — 1 read-write *or* N read-only per file):** the leader
   ingest process is the **sole writer** of Hive-partitioned Parquet (atomic swap); API workers
   read **Parquet directly, read-only** (per-worker in-memory DuckDB), never the writer's file.
   Redis fronts point reads only — analytical SQL runs against Parquet ([modules/22](../modules/22-data-ingest.md) §7, [modules/16](../modules/16-infrastructure.md) §3/§10).
3. **FinMind Sponsor quota:** cache-first (T+1 data cached per dataset/stock/date) + per-user
   fair-share within the pooled 6000/hr token + a documented capacity model.
4. **Scheduler HA:** leader election / locking job store, at-least-once + idempotent delivery,
   retry/DLQ, `running` job status.
5. **Cost/abuse ceilings:** per-turn max-steps + max-cost kill-switch; BYOK lifts only the LLM
   limit; provider + scraper circuit breakers with timeout/retry/error-class rules.
6. **Observability:** distributed tracing on the `turn_id/agent_id/node_id/seq` envelope;
   SLIs/SLOs; scraper-drift monitoring; dashboards/alerting.
7. **Secrets:** KMS envelope encryption + rotation; decrypt in-process at call time only.
8. **Migrations & lifecycle:** Alembic (dual-dialect CI); time-partitioning + concrete
   retention/purge (see database-schema).
9. **Concurrency model (modules/16 §10):** every shared-state hot path has a declared safety
   mechanism for stateless multi-worker operation — atomic upserts/`INCR` (counters, rate/quota
   check-and-increment), reserve-before-act budgets, single-writer ordering (event_seq,
   subscriptions), idempotency keys, optimistic `version` CAS (threads/active-leaf), one-active-
   turn-per-thread, cache single-flight, per-call entitlement. No path assumes single-process.

## Consequences
- Adds Redis + a broker/queue + DuckDB/Parquet to the stack (previously only SQLite/Postgres).
  Documented in modules/16 and the build phases.
- API workers become fully stateless; scheduler becomes an HA background tier.
- No governance change — caching keeps caveats, secrets stay encrypted, isolation namespaced.
- Build phases (architecture.md §11) gain an explicit operations/deployment phase before hardening.
