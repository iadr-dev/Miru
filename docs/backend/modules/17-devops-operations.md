1	# Module: DevOps & operations (delivery + run lifecycle)
2	
3	Module 16 defines the **architecture** (what runs, how pieces talk). This module defines the
4	**delivery and run lifecycle** — CI/CD, packaging, environments, probes, shutdown, DR,
5	residency, cost ops, and runbooks. From the DevOps review (see [../adr/0016](../adr/0016-devops-operations.md)).
6	
7	## 1. CI/CD pipeline
8	
9	Python/FastAPI monorepo. Gates, in blocking order:
10	
11	- **PR gate (fast, blocking):** ruff + black → mypy (strict) → `fast` unit → `default-ci`
12	  (non-network, fixtures) → **governance suite** (non-trading, caveat, per-user isolation,
13	  `sponsor` gate) → **dual-dialect Alembic test** (apply **+ rollback** on both SQLite and a
14	  Postgres service container) → secret scan (gitleaks — 35+ key env vars) + dep vuln
15	  (`pip-audit`) + image scan (Trivy) + SBOM.
16	- **Merge / nightly:** `full-non-network` + coverage.
17	- **Scheduled / pre-release (network, gated, low-quota tokens):** `operator-preflight` (live
18	  source reachability) + `browser-e2e` — hit FinMind/Brave/scraper quotas, so cron only, never
19	  per-PR.
20	- **Scraper contract canary (cron):** run versioned parsers against recorded + a small live
21	  sample and diff → catches MOPS/PTT/ETF-PCF/Brave drift before users do (operationalizes
22	  [16](16-infrastructure.md) §7 monitoring).
23	- **Deploy:** blue-green or canary; **migration order = expand → deploy → contract-later**
24	  (never a breaking migration in-place). Rollback = redeploy previous image + (if needed)
25	  down-migration; expand-contract keeps rollback safe.
26	
27	## 2. Containerization & runtime
28	
29	- **One image, role-selected entrypoint** (`api` | `scheduler` | `worker` via env) — parity
30	  across the three process types ([16](16-infrastructure.md) §1). Non-root, pinned base,
31	  `.dockerignore` excludes fixtures/secrets.
32	- **`docker-compose` (dev parity)** brings up the **full** stack: api + scheduler(leader) +
33	  worker + Redis + Postgres + Parquet/DuckDB volume + broker — so the Redis-transport path is
34	  exercised locally, not first discovered in prod.
35	- **Ollama** = optional compose profile (large/GPU); the offline/self-host path, **not** in the
36	  hosted prod image.
37	
38	## 3. Environments
39	
40	- **dev / staging / prod** config matrix. Staging uses **Paddle sandbox** (`PADDLE_ENVIRONMENT`) and a
41	  **separate FinMind token** (must not consume prod's pooled 6000/hr).
42	- FinMind has no sandbox → staging runs mostly on fixtures (non-network profiles) + a tiny live
43	  token. Staging **seed data** (accounts/tiers/threads) exercises entitlement gating + a
44	  Paddle-sandbox webhook-replay procedure.
45	
46	## 4. Probes & lifecycle
47	
48	- **`/livez`** (process up), **`/readyz`** (Redis + Postgres + broker reachable — refuses LB
49	  traffic if not), **`/startupz`**. These are **distinct from `/health`** (which is business
50	  data-currency + FinMind quota, not an infra probe). Scheduler/worker have their own liveness
51	  (leader-lock held? queue consumable?).
52	- **Graceful shutdown / SSE draining:** on SIGTERM → stop accepting new turns → checkpoint or
53	  emit `turn.end` for in-flight turns → hold `terminationGracePeriodSeconds` (sized to P99 turn
54	  or a hard cap) → LB deregistration delay + `preStop`. Define whether a killed turn resumes on
55	  another worker (via `stream_events` replay) or restarts, and how the per-turn cost meter
56	  reconciles so reconnect+resume isn't double-charged.
57	
58	## 5. Reliability / DR
59	
60	- **Postgres:** managed PITR + **tested restore drill**; defined RPO/RTO. Holds the auditable
61	  record (governance §7/§9) — non-negotiable backup.
62	- **Redis HA + degradation tiers** (Redis is now critical-path — transport + counters + locks +
63	  cache): Sentinel/managed-cluster failover. On Redis loss: SSE falls back to `stream_events`
64	  DB polling; quota checks **fail-closed** to conservative per-tier limits; locks fall back to
65	  **Postgres advisory locks**. Per-keyspace persistence: cache keys ephemeral, but **quota
66	  counters + idempotency locks need AOF or a Postgres-authoritative fallback** so a flush can't
67	  cause double-billing/double-delivery.
68	- **DuckDB/Parquet:** reconstructible cache of upstream data → document rebuild runbook + time
69	  (a cold rebuild re-hits FinMind quota) rather than backup.
70	- **Graceful degradation** when FinMind / a provider / a scraper is down → M5Q reports it;
71	  circuit breakers ([16](16-infrastructure.md) §6) prevent cascade.
72	
73	## 6. Cost ops (infra/vendor — complements app-level `usage_events`)
74	
75	- LLM spend dashboards **per provider/role/tier** and **BYOK vs platform** (platform eats
76	  non-BYOK inference); budget alerts **before** the per-turn ceilings are the only backstop.
77	- FinMind pool utilization + a cost/capacity alert at the saturation point ([16](16-infrastructure.md) §4).
78	- Brave spend, cloud infra (Redis/Postgres/compute/egress), Paddle fees → budget-alert channel.
79	- These are monthly-bill risks a runaway loop or viral user triggers; the per-turn kill-switch
80	  protects a turn, not the bill.
81	
82	## 7. Security & compliance ops
83	
84	- **Ingress TLS** (M6D §5 is *outbound*): managed certs, TLS 1.2+ min, HSTS at the edge.
85	- **Data residency / PII (PDPA):** `users` holds birthday/phone/nationality → choose a TW/APAC
86	  region; **assert PII never enters LLM prompts/journal/logs** (extends governance §9); publish
87	  a **sub-processor inventory** (7 LLM vendors + Brave + FinMind + Paddle + OAuth + LINE/Telegram/
88	  email) + backup residency. Paddle (merchant of record) handles payment PII; profile PII is ours.
89	- **Log redaction:** no raw payloads / secrets / PII in log sinks or shipping — a startup
90	  self-check refuses to boot if the mandatory guardrail hooks aren't registered.
91	- **Audit-log shipping:** `journal_entries` is the audit record — define retention (see
92	  [../database-schema.md](../database-schema.md)), immutability/tamper-evidence, and off-box shipping.
93	- **GDPR/PDPA account deletion** flow: purge/anonymize across all `user_id`-scoped tables.
94	
95	## 8. Runbooks, on-call, feature flags
96	
97	- **Runbooks:** scraper breakage (detect drift → pin last-good parser → serve M5Q-degraded →
98	  patch versioned parser → canary → redeploy); Redis failover; FinMind pool saturation; provider
99	  outage / fallback-chain exhaustion; **Paddle webhook outage** (entitlement is webhook-sourced →
100	  backlog delays upgrades → replay/reconcile against Paddle).
101	- **On-call / incident severities** defined; alerting routes to the channel.
102	- **Feature flags** (config-driven, hot-reload where safe): dark-launch scrapers, toggle
103	  `AITW_WEBSEARCH` backend, gate new skills, **kill a misbehaving source without a deploy**.
104	- **MCP deployment surface:** separate process/port vs same image role, principal auth
105	  (API key / anonymous — [06](06-interfaces.md)), and rate-limiting like the REST tier — an
106	  externally-exposed surface, spec its exposure explicitly.
107	
108	## Governance obligations
109	- Nothing here relaxes governance; it operationalizes it (log redaction, PII residency,
110	  non-trading self-check, audit shipping).
111	