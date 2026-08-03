# ADR 0025 — Platform data-ingest / ETL layer + governance §9c carve-out

## Status
Accepted. Adds [modules/22](../modules/22-data-ingest.md) + **governance §9c**; reuses scheduler
HA + cache tier ([modules/16](../modules/16-infrastructure.md) §3/§4/§5); feeds analytics
([modules/20](../modules/20-analytics-scoring.md)), context (02/04), calendar (18).

## Context
Downstream features (analytics/scoring, screener, calendar, fast context reads) assume a
**populated historical store** that the gov OpenAPIs cannot provide — they are latest-snapshot
only, no backfill. FinMind is the only date-range engine. So the platform must **poll daily and
store**. But this is exactly a **market-wide scan on a timer with no user in the loop**, which
governance §1.3 forbids ("no auto-polling, no full-market scans"), and it is **not** covered by
§9a (user-authorized jobs) or §9b (demand-driven live quotes). It needs its own carve-out.

## Decision
Add an `ingest/` module — the **sole writer** of the derived DuckDB/Parquet + Redis store — and
a new **governance §9c platform-ingest carve-out**:
- **Whole-market-by-date is mandatory** — FinMind without `data_id` (≈1 call/dataset/day, ~25–35
  calls/day total); per-symbol market-wide ingest is banned (blows quota). Requires **≥Backer**
  (Sponsor for 分點/官股/產業鏈/主動ETF/tick). Gov OpenAPI only for same-day-authoritative surfaces
  FinMind lags (處置/注意, ETF NAV, sub-indices).
- **Job groups** — daily-postclose, daily-late (21:00 staggered), weekly, monthly (deadline
  polling), event (coarse scraper pollers + theme/score compute batch), backfill (checkpointed).
- **Idempotent + self-healing** — upsert on natural PKs (replace-partition-by-date); detect
  missed days by diffing `TaiwanStockTradingDate` vs stored dates → targeted gap backfill;
  next-morning reconcile for late-arriving datasets.
- **Trading-calendar gated** — every cron gated on the trading-day list + NCDR typhoon overlay.
- **Three scheduled classes, separate quota buckets** — (A) platform ingest §9c / (B) §9b live
  poller / (C) §9a user jobs must not share a bucket (fair-share, infra §4). **Backfill is a
  throttled sub-bucket *under* (A)**, isolated so a backfill can't starve live users — it is not a
  fourth class. (See §Consequences + module 22 §0.)
- **Store = DuckDB/Parquet + Redis** (infra §3). DuckDB's documented model is **one read-write
  process OR many read-only processes per file — never both** (MVCC in-process only), so the
  leader is the **sole writer** of **Hive-partitioned Parquet** (atomic temp-dir→rename + manifest
  swap) and **API workers read the Parquet directly, read-only** (per-worker in-memory DuckDB) —
  never the writer's `.duckdb` file. Redis fronts *point* reads; the analytical screener/scoring
  SQL always runs against Parquet. **ClickHouse** migration trigger = concurrent-writer / seconds
  write-read latency / partition-rewrite volume — **not** read fan-out ([22](../modules/22-data-ingest.md) §7).

**Governance §9c** (mirrors §9a/§9b structure): purpose-bounded (build the platform's own store
from date-range sources + snapshot latest-only gov feeds), cadence-bounded (fixed post-publication
crons, coarse scrapers with backoff/circuit-breaker, no tight loops), calendar-bounded,
not-user-data + non-trading (writes the shared derived store, caveats preserved), quota-fair
(own bucket), auditable + leader-elected (every run journaled), secrets env-only.

## Consequences
- Ingest is written down as its own carve-out, not smuggled under §9a — it's *more* permissible
  than ad-hoc user polling (cache-building, not per-user observation) but was unauthorized by the
  letter of §1.3, so it must be explicit.
- Steady-state daily ingest fits Sponsor 6000/hr with ease; backfill is the only pressure and
  runs off-peak in its own bucket.
- The three scheduled-activity classes (A ingest / B live poller / C user jobs) are now cleanly
  separated in lane and quota — a recurring source of conflation in prior reviews.
- Adds ingest workers to the scheduler tier (reuses leader election + at-least-once), versioned
  scrapers + canaries (infra §7), and a preflight that fails loud on a Free token.
