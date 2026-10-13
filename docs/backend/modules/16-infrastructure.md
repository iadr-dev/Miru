# Module: Infrastructure & operations (`backend/src/`, deployment)

The hosted/distributed-systems layer. The agent design is single-process-friendly, but the
product is **multi-user hosted**, so several mechanisms specified as "in-process" need a
cross-process story before build. This module is the source of truth for that. Raised by the
Backend Architect + Database Optimizer reviews.

## 1. Deployment topology

```
                    ┌───────── Load balancer ─────────┐
                    ▼                                  ▼
        API workers (uvicorn, N)              API workers …
        - FastAPI REST + SSE                  (stateless; any worker serves any request)
        - agent runtime / orchestrator
              │            ▲ SSE fan-out
              ▼            │
        ┌───────────── Redis ─────────────┐   ← cross-process transport + hot cache + locks
        │ pub/sub (live event fan-out)     │
        │ quota counters / rate limits     │
        │ idempotency locks                 │
        │ hot cache (snapshots, health)     │
        └───────────────────────────────────┘
              │                    │
        PostgreSQL (app state)   DuckDB/Parquet (derived market data, columnar)
              │
        Scheduler worker (alerts, jobs, delivery)  +  Ingest worker (market-data pulls → Parquet)
```

- **API workers** are stateless — any worker serves any request (including SSE reconnect),
  which is why live events must go through Redis, not an in-process bus (see §2).
- **Scheduler workers** run the user-authorized jobs/alerts (the scheduler carve-out in `backend/CLAUDE.md` §4); the target is ≥1 with leader election (see §5).
- **Process types** (`backend/docker-compose.yml`): api, scheduler, ingest (plus an MCP server). There is no separate task queue. Scale api horizontally; the scheduler and ingest are single-active (leader election is not yet real, §5).

## 2. Cross-process event transport (fixes in-process bus at scale)

The event bus ([10-events-hooks.md](10-events-hooks.md)) is in-process **within one turn's
worker**. But `POST /agent/query` runs a turn on worker A while `GET /threads/{id}/stream`
(reconnect) may hit worker B. So the **live SSE fan-out goes through Redis Pub/Sub** keyed by
`turn_id`:

- The turn's worker publishes each enveloped event to `stream:{turn_id}` (Redis) **and**
  appends to the durable `stream_events` table.
- Any worker serving `GET /threads/{id}/stream` subscribes to `stream:{turn_id}` for live
  events, and replays missed events from `stream_events` by `event_seq` on `Last-Event-ID`.
- In-process bus remains a same-worker optimization; Redis is the cross-worker source of truth
  for the live path. `stream_events` is the durable replay log.

## 2a. Live-quote feed — shared demand-driven poller + SSE fan-out

Premium/Pro intraday quotes (`price_intraday`) are pushed, not client-polled — the scaling-safe
design (1 poll → N clients instead of N clients × N polls against the 6000/hr Sponsor pool).

```
client opens  GET /quote/stream?symbols=2330,0050&market=TW   (SSE; reuses §2 transport)
   │  subscription registered in Redis: demand{TW}: {2330, 0050, …} (refcounted per connection)
   ▼
shared quote poller (one worker):  every 5s, for each symbol with ≥1 subscriber & market-open:
   fetch snapshot via dataaccess router (price_intraday → finmind/twse_mis) → cache →
   publish to Redis  quote:{market}:{symbol}
   ▼
each API worker's SSE handler subscribes to its connection's quote:* channels → pushes `quote` events
   │  client disconnects → refcount--; at 0 the symbol leaves the demand set → poller stops it
```

- **Transport = SSE + Redis** (the same fan-out as agent chat, §2) — a quote feed is
  server→client only; subscribe/unsubscribe is just the stream's `symbols` param (or a small
  `POST /quote/subscribe`), so no WebSocket is needed. Detail page = 1 symbol; watchlist = N;
  an agent `rich_card` quote piggybacks the same mechanism.
- **Demand set = union of all live subscriptions** (Redis refcount). The poller **only** touches
  symbols someone is watching, **only** in market hours, **auto-stops** on last disconnect
  — the live-quote poller's bounded observation, not an unattended scan (`backend/CLAUDE.md` §4).
- **Caps:** per-connection symbol cap + global demanded-symbol cap; feeds the FinMind Sponsor
  quota manager (§4) — over-budget → slower cadence / cached, never a tight loop.
- **Cadence tiers (client hint).** The stream accepts a `cadence` hint on subscribe — `normal` (~5s,
  default) or **`saver`** (a slower snapshot interval, e.g. ~20–30s) for **mobile data-saver / metered
  connections**; the client requests `saver` on cellular/low-power and the poller serves that symbol at
  the coarser interval (one poll still fans out to all subscribers at that symbol's slowest requested
  cadence). A backgrounded mobile app **unsubscribes** entirely (refcount→0 stops the poll) and
  revalidates on foreground — see [mobile interaction-spec §6.1](../../mobile/interaction-spec.md). The
  delayed-snapshot caveat is unchanged; `saver` just widens the interval, never fabricates.
- **Entitlement:** `/quote/stream` intraday is `sponsor` (Premium/Pro); an unentitled client gets
  the masked teaser / EOD only. Payload keeps the delayed-snapshot caveat (not true real-time).
- **Off-hours / no subscribers:** poller idle; `/quote` (non-stream) still serves last snapshot/EOD.
- **Payload freshness metadata (for the client `DataFreshnessBadge` + off-hours handling):**
  every `quote` event carries **`as_of`** (the snapshot's source timestamp, not the publish time) and
  **`market_open`** (bool). The client stamps the badge from `as_of` and, once `market_open=false`,
  **stops treating the last tick as current** (shows last-close/EOD, static) — so a frozen quote after
  close never implies a still-ticking market. `as_of` is also the client's **monotonic discard
  validator** (drop any tick not newer than what's shown).

## 3. Derived market-data store & cache tier (`MarketDataStore` impl)

Market data is **not** in the app Postgres DB. It lives in a dedicated tier — the concrete
implementation of the **`MarketDataStore` protocol** ([02-packages.md](02-packages.md)),
config-selected like every other store:

| Layer | Tech | Holds | TTL / refresh |
|-------|------|-------|---------------|
| **Columnar store** | **DuckDB over Parquet** | EOD/history, financials, holdings, the normalized `etf_holdings(etf_id,stock_id,weight,shares,date)`, canonical package versions | `official-eod` → refresh post-close; immutable canonical is versioned, never mutated |
| **Hot cache** | **Redis** | source-health, latest snapshots, freshly-built conversation packages, sponsor-dataset reads | short TTL by `timing_class`: `reference` sec–min; `live-ish` seconds; `official-eod` until next close |

- **Read-through & layering:** the cache is a **caching `SourceAdapter` wrapper in `sources/`**,
  so the "only `sources/` does I/O" invariant (`AGENTS.md`) holds. `context/` calls
  `MarketDataStore` abstractly and never opens Redis/DuckDB/sockets itself. Path: `context/` →
  `MarketDataStore` (Redis hit → DuckDB hit) → on miss the caching adapter fetches via the real
  source adapter and writes back. `get_canonical_context` is "(cached, no network)" because of
  this tier.
- **FinMind `storage_objects`** whole-day parquet (tick/minute-K/分點) lands directly in the
  Parquet store.
- **Cache key = `(capability, market, symbol[, date])`** so multi-market data (TW/US/JP…) never
  collides; **invalidation** keyed on canonical version + `timing_class` (live-ish never cached
  beyond seconds). Staleness surfaced honestly via M5Q, never hidden.
- **Single-flight on miss (stampede guard):** the first request to miss a key takes a per-key
  Redis lock and fetches; concurrent misses for the same key **await that result** instead of
  each hitting the source. Critical for the FinMind Sponsor pool — a hot symbol = 1 upstream
  fetch, not N (§10 concurrency).
- **Reader/writer isolation (DuckDB's one-writer-OR-many-readers rule,
  `duckdb.org/docs/stable/connect/concurrency`):** the **leader ingest process is the sole DuckDB
  writer** and emits **Hive-partitioned Parquet** (`market=…/date=…`) via an atomic
  temp-dir→rename + manifest swap ([22](22-data-ingest.md) §4). **API workers are read-only**
  consumers that open the **Parquet directly** (per-worker in-memory DuckDB,
  `read_parquet(glob, hive_partitioning=true)`) — **never the writer's `.duckdb` file** (opening a
  file the leader holds read-write throws a lock error; the two modes are mutually exclusive per
  file). Redis fronts **point** reads only; the **analytical screener/scoring SQL always executes
  against Parquet** — it is not a cacheable key, so "Redis makes read-concurrency moot" does **not**
  hold. Read-only Parquet is what scales the reader fan-out ([22](22-data-ingest.md) §7).

## 3a. Read-model freshness contract (client cache-coherence)

So a client cache can never extend a value's freshness budget
(the coherence invariant), the read surface exposes the server's own currency, cheaply:

- **`as_of` on every cacheable read.** Each `GET /quote`, `/analytics/*`, `/market/*` response
  carries an **`as_of`** timestamp = when the served value was produced (ingest/compute time), plus
  the `timing_class` it belongs to. This is the client's **monotonic validator** — it discards any
  response not newer than what it holds, and stamps the `DataFreshnessBadge`. `as_of` reflects the
  cache key's canonical version (§3), not the HTTP hit time.
- **Conditional GET (`ETag` / `If-None-Match`).** Cacheable reads return a strong **`ETag`** derived
  from the canonical version; a client revalidate sends `If-None-Match` → **`304 Not Modified`** (no
  body, no re-render) when nothing changed. This makes boundary revalidation *cheap* — the client
  polls the boundary but only pays for a payload when the value actually advanced.
- **Batch-completion invalidation signal.** When a derived-compute batch lands
  (`compute-scores` / `compute-topN` / `compute-ai-summary` / `discover-themes`, [22](22-data-ingest.md)
  §2), the ingest leader publishes a lightweight **`data.updated`** event over the §2 transport
  carrying `{capability, market, as_of, symbols?|scope}`. API workers relay it on the client's SSE
  connection so the frontend **invalidates exactly the affected keys** (`revalidateTag`) the moment
  fresh analytics exist — instead of waiting out a time boundary. Absent a subscription, the
  boundary-anchored `revalidate` (fixed batch time) remains the correct floor.
- **`Cache-Control`.** Cacheable reads send `private, max-age=0, stale-while-revalidate=<to class
  boundary>` — the browser serves the last good value instantly and background-revalidates; the
  intraday stream is `no-store` (it's live SSE, not a cacheable body). The **single per-class TTL**
  lives server-side and is echoed to the client so RSC/TanStack/HTTP layers mirror one budget, never
  sum three (the client-side rule).
- **Entitlement-keyed reads.** A sponsor-gated read's cache entry is keyed by entitlement so a tier
  change busts the cached **masked** payload (per-call entitlement, §10) — an upgraded user never
  keeps seeing the 馬賽克 cell.

## 4. FinMind Sponsor quota — caching + fair-share (fixes the shared-token ceiling)

A single Sponsor token = **6000 req/hr pooled across all paid users** (~1.67/s) — a hard
ceiling. Mitigation (also [12-multi-user-tiers.md](12-multi-user-tiers.md) §4):
- **Cache-first:** Sponsor EOD data (分點/官股/產業鏈) is T+1 and changes once/day → cache per
  `(dataset, stock, date)` in DuckDB/Redis; repeat reads never re-hit FinMind. The vast
  majority of the 6000 budget should be cache misses only.
- **Fair-share: not yet built.** The real reserver (`RedisFinMindPoolReserver`,
  `usage/finmind_pool.py`) is a purely **global** rolling-window counter — its atomic
  `INCRBY`/`DECRBY` reservation is keyed on `(key_prefix, window bucket)` only, with no `user_id`
  dimension, no per-user sub-quota, no priority, and no queuing. One heavy user's fan-out can
  exhaust the whole pool for every other paid user today; only the global reserve-before-act
  ceiling (grant up to headroom, refund the overage) is enforced.
- **Capacity model:** document the paid-user count at which one token saturates and the plan
  (multiple Sponsor tokens, request coalescing). The ceiling must be visible, not discovered
  in production.

## 5. Scheduler HA & delivery semantics (fixes SPOF)

[14-scheduler-alerts.md](14-scheduler-alerts.md) runs ≥1 scheduler worker:
- **Leader election / locking job store** so multiple workers run safely without double-firing.
  The Postgres-advisory-lock lease is **built for the ingest writer** ([22](22-data-ingest.md) §6c);
  the scheduler still resolves to `AlwaysLeader`, so only a single scheduler worker is safe today.
  The notification dedupe key below is the guard against duplicate delivery.
- **At-least-once execution + idempotent delivery:** dedupe key `rule_id:occurrence_id:channel`
  in `notifications` ([14](14-scheduler-alerts.md) §5); a redelivered LINE/Telegram/web native is a no-op.
- **Retry + DLQ: designed but not yet implemented.** `scheduler/` has no retry-queue or
  dead-letter logic today — grepped the whole package, none exists. `DeliveryReceipt.retryable`
  (`scheduler/delivery/base.py`) is a boolean field nothing currently consumes; the field's own
  docstring frames `retryable=True` as a documented future seam, not implemented behavior.
  `scheduler/worker.py::_log_receipt` just logs the receipt (channel, `delivered`, `retryable`) —
  a transient channel failure is observed, never retried or routed to a dead-letter path.
  **`running` status** is added to `schedule_jobs` to detect stuck/orphaned runs after a crash.

## 6. Concurrency, cost & abuse ceilings

- **FastAPI I/O model:** the source/tool I/O path is async end-to-end; genuinely blocking
  adapters (scrapers, some SDKs) run in a thread/process pool so they never block the event
  loop / SSE. ASGI worker count + pool sizing are config.
- **Per-turn hard budget:** beyond the concurrency cap, a **max steps + max total cost/tokens
  per turn** kill-switch (not just parallelism) bounds runaway fan-out/loops
  ([11-orchestration.md](11-orchestration.md) §5).
- **BYOK lifts only the LLM chat limit** — FinMind quota, web-search, scraper load, and
  request-rate limits still apply (platform-side cost). See [12](12-multi-user-tiers.md) §5.
- **Provider resilience:** per-call timeout budgets, retry-with-backoff, error classification
  (rate-limit/5xx/timeout → fallback vs fail), and a **circuit breaker** per provider and per
  scraped source ([04](04-llm-adapter.md) fallback chains).

## 7. Observability (target — structlog logging exists; tracing, SLOs and dashboards are not built)

- **Distributed tracing** across API → orchestrator → sub-agents → tools → sources → LLM,
  using the SSE **correlation envelope (`turn_id`/`agent_id`/`node_id`/`seq`) as the trace id**.
- **Structured logs** with those correlation IDs; **SLIs/SLOs**: turn latency, SSE
  time-to-first-token, tool error rate, provider latency, FinMind pool utilization,
  scraper-parse success rate.
- **Scraper-drift monitoring** (MOPS/PTT/ETF-PCF/Brave): detect silent parse corruption
  (parser returns empty/degenerate) — distinct from M5Q staleness — with alerting + versioned
  parsers + canaries. Treat scraped sources as a monitored subsystem.
- **Dashboards + alerting** on the above; per-skill quality already covered by feedback ([13](13-chat-interaction-features.md)).

## 8. Secrets & auth config

- **OAuth (sign-in):** `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/`GOOGLE_REDIRECT_URI`;
  `LINE_CHANNEL_ID`/`LINE_CHANNEL_SECRET`/`LINE_REDIRECT_URI` (LINE **Login** channel). PKCE
  `code_verifier` + `state` are per-request (Redis/`oauth_flows`, not env). **JWT session:**
  `JWT_SECRET_KEY`/`JWT_ALGORITHM`/`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (default 15 — the
  access token is in-memory + refreshed; short expiry makes revocation fast)/`JWT_REFRESH_TOKEN_EXPIRE_DAYS`.
  Storage/rotation model: [modules/12](12-multi-user-tiers.md) §1b.
- **Notification channels (separate from Login):** `LINE_MESSAGING_*` (Messaging API — the
  `DeliveryChannel`, distinct channel from LINE Login), `TELEGRAM_BOT_TOKEN`/`_WEBHOOK_SECRET`.
- **Data providers:** `FINMIND_API_KEY`, `FINNHUB_KEY`, `BRAVE_SEARCH_API_KEY`. **Reserved for
  phased US/macro adapters** (not yet specified in the source inventory — [architecture §3](../architecture.md)):
  `ALPHA_VANTAGE_API_KEY`, `FRED_KEY`, `FMP_API_KEY` — wire only when their adapters land ([19](19-data-access-router.md) phased markets).
- **LLM:** the 7-provider keys ([04](04-llm-adapter.md)); `PADDLE_*` billing.
- **Handling:** all above are **KMS envelope-encrypted** platform secrets (not a static app
  key); documented key-rotation; DB-at-rest is separate from field-level (`user_secrets.ciphertext`
  for BYOK/per-user). Secrets decrypt **in-process at call time only**, never cross the
  sub-agent/scheduler boundary in plaintext, never logged (`backend/CLAUDE.md` §8, security).

## 9. Migrations & data lifecycle

- **Migrations:** none yet, by design (development stage): `create_all()` + the additive upgrade keep the schema current and a destructive change rebuilds the dev database. **Alembic is adopted at the first production release**; from then every migration must apply on **both SQLite and
  Postgres** in CI (expand-contract for evolving the message-tree / `stream_events` schema).
- **Retention / partitioning (planned, not implemented):** the tables are the SQLAlchemy models in
  `backend/src/db/models/`. Target: `usage_events`/`journal_entries`/`billing_events` time-range
  partitioned on Postgres (monthly; cheap detach/drop); **`stream_events` TTL-purged, NOT partitioned**
  (partitioning would break its `UNIQUE(turn_id, event_seq)` guard). Account erasure is built
  (`accounts/erasure.py`).

## 10. Concurrency model (the single guarantee for shared state)

API workers are stateless and horizontally scaled, so **every shared-state hot path has a
declared concurrency-safety mechanism** — no path relies on "one process at a time." Matrix:

| Contended path | Hazard | Mechanism |
|----------------|--------|-----------|
| `usage_counters` increment | lost update (parallel turns, same user/window) | atomic `INSERT … ON CONFLICT (pk) DO UPDATE SET n = n+1` ([10](10-events-hooks.md) §4) |
| Rate-limit / daily-chat gate | two concurrent turns both pass the check | check-and-increment in the **same atomic upsert** (Redis `INCR` + TTL, or the counter upsert), not read-then-write |
| Quota check (chat/day, FinMind) | TOCTOU — both admitted over the cap | reserve **before** work (atomic decrement of a Redis budget); refund on failure. Reservation fires from two `orchestrator.py` call sites: `_start_fanout` (the `subagent`/`team` frameworks, via `_run_subagent`/`_run_team`) reserves per fan-out lane, and — closing a scope gap that previously left it unreserved — `_run_single` also reserves directly whenever its resolved skill's tool allowlist includes a Sponsor-tier tool (`_has_sponsor_tool`), stripping those tools from the loop if the reservation comes back degraded |
| `subscriptions` (Paddle) | out-of-order / duplicate webhooks | single writer + `occurred_at` ordering guard + `billing_events` idempotency ([15](15-payments-paddle.md)) |
| `threads` (rename/star/archive, multi-device) | lost update | optimistic `version` compare-and-set (409 on stale) |
| **Thread active-leaf / branch switch** | two devices edit/switch same thread | CAS on `threads.active_leaf_message_id` keyed by `version`; last-writer-wins with 409 retry |
| **Concurrent turns in one thread** | two `POST /agent/query` on same thread interleave | **Partially built.** The DB has the guard: a partial-unique index on `turns` (`status='running'`, `uq_turns_one_running_per_thread` in `db/models/`). **But no request-path code ever writes `status='running'`** — `ConversationStore.append_turn` always inserts a turn with `status=TurnStatus.DONE` (`conversation/store.py`), so the constraint is present but never exercised, and `POST /agent/query` does not check `TurnRegistry.active_for_thread` before starting a second turn — today two concurrent requests on the same thread can both proceed. |
| `messages.seq` / `stream_events.event_seq` | gap/collision across parallel sub-agent lanes | assigned in-process by the **single event-bus writer per turn**; `UNIQUE(turn_id, seq)` guard |
| Idempotent turn create | double-submit / retry | **Partially built.** The DB constraint is real (`uq_turns_user_idempotency_key`), and a separate `conversation.turn_lifecycle.TurnLifecycle` class implements the full in-process-dedupe-map + durable-constraint-backstop pattern — but `POST /agent/query` (`api/routers/agent.py`) does **not** use `TurnLifecycle`; it calls `ConversationStore.append_turn` directly, which does not check for an existing `idempotency_key` first and does not catch the constraint's `IntegrityError` — a real double-submit against the live HTTP route hits an unhandled 500, not a graceful replay. No Redis lock exists anywhere in this path. |
| Scheduler jobs | multiple workers fire same job | leader election / locking job store; at-least-once + idempotent delivery dedupe (§5) |
| Live-quote poller | duplicate polls for a symbol | **per-worker** poller (not single-shared); demand set is a Redis refcount enforcing the fleet-wide cap (§2a) |
| **Cache fill (market data)** | stampede — N requests miss + all fetch same key | **Built.** `CachingSourceAdapter` (`sources/caching_adapter.py`) is a real read-through Redis wrapper — single-flight-on-miss via `SET NX EX` lock + poll-wait, compare-then-delete release (token-based) so a stale holder can't release another waiter's lock. Enabled by default in the real deployment (`docker-compose.yml`'s `MARKET_DATA_CACHE_BACKEND: ${MARKET_DATA_CACHE_BACKEND:-redis}`); wired in `dataaccess/composition.py::build_default_adapters` whenever `Settings.market_data_cache_backend == "redis"`. Does not cover the ingest leader's own whole-market pulls, which construct an unwrapped adapter directly (§3) — this cache tier is for the per-symbol request path only |
| **Market-data store (DuckDB/Parquet)** | leader writer + API-worker readers on one file → **lock error / torn read** (DuckDB: 1 read-write *or* N read-only per file; MVCC in-process only) | **Readers = read-only Parquet** (`read_parquet` glob, per-worker `:memory:` DuckDB — no shared file lock). **Writer = leader only**; partition writes are **temp-dir → atomic rename + manifest pointer** so a glob never straddles a swap; startup sweeps orphaned temp dirs. DuckDB version pinned ([22](22-data-ingest.md) §7) |
| **Watchlist reorder (item/group), multi-device drag** | two devices reorder same list → lost update / flicker | **lexorank `sort_order` (fractional key)** — insert *between* neighbors, no neighbor rewrite; equal-midpoint collision → jitter+retry; bulk reshuffle uses `POST /watchlists/reorder` full-array + parent `version` CAS |
| **Watchlist group rename, multi-device** | lost update | optimistic `version` CAS (409 on stale), as `threads` |
| **Watchlist star toggle (add/remove item)** | double-tap / retry → dup or add↔remove race | `PUT`=`INSERT … ON CONFLICT (watchlist_id, symbol) DO NOTHING`; `DELETE` idempotent; partial-unique `(watchlist_id, symbol)` is the guarantee |
| **Offline action queue (mobile)** | app offline → user stars/reorders/sets alerts → replayed on reconnect out-of-order or twice | each queued action carries a **client `op_id`** (idempotency key) + the entity `version` it was based on; on reconnect the client replays **in order**, server dedupes by `op_id` and applies the existing per-entity guard (star `ON CONFLICT DO NOTHING`, reorder/rename `version` CAS → **409 surfaces as a per-op conflict** the client re-reads, not a silent overwrite). No new server primitive — it composes the existing idempotency + CAS mechanisms; the queue lives client-side ([mobile interaction-spec §6](../../mobile/interaction-spec.md)) |
| SSE fan-out across workers | event delivered to wrong/no worker | Redis pub/sub keyed by `turn_id`; `event_seq` replay (§2) |
| **Entitlement change mid-turn** | tier downgrades while a turn runs | entitlement resolved **per tool call** (pre_tool hook), not cached for the turn — a mid-turn downgrade masks the next `sponsor` call, not retroactively |
| **BYOK key edited mid-request** | key removed while in use | key read at call time; a removed key fails that call gracefully (fallback/limit), never a torn read |
| Steer vs cancel vs running turn | user steers/cancels while a step runs | steering queued, applied at next `step_start`; cancel sets a flag checked between steps; in-flight read-only tool finishes ([07](07-conversation-streaming.md)) |

**Principles:** (1) prefer **atomic DB/Redis ops** (`ON CONFLICT`, `INCR`, CAS) over
read-modify-write; (2) **reserve-before-act** for budgets/quotas (avoid TOCTOU); (3) **single
writer** where ordering matters (event_seq, subscriptions); (4) **idempotency keys** on every
externally-retryable mutation (turns, webhooks, deliveries); (5) **optimistic `version`** for
user-facing multi-device edits, surfacing 409 for client retry. SQLite (dev) is single-writer so
these are latent there; they are load-bearing on Postgres/Redis in prod. Covered by
concurrency/load tests ([17](17-devops-operations.md) §1).

## Governance obligations
- Nothing here weakens governance (`backend/CLAUDE.md` §4, §8): caching preserves currentness caveats; secrets stay
  encrypted; per-user isolation holds across cache/Redis keys (namespaced by `user_id`).

## Postgres engine settings

`db/base.py::make_engine` gives a Postgres engine `pool_pre_ping` (a stale connection after a restart
or failover is replaced at checkout instead of surfacing as an error), a bounded pool
(`DB_POOL_SIZE` + `DB_MAX_OVERFLOW`, per process, so size them against the server's
`max_connections` across all replicas), `DB_POOL_RECYCLE_SECONDS` for idle connections, and a
server-side `statement_timeout` (`DB_STATEMENT_TIMEOUT_MS`, 0 disables) so a runaway query is
cancelled instead of holding a connection. SQLite keeps its defaults. The API disposes the pool on
shutdown.

Two more limits keep one stuck client from taking the whole pool down:

- `DB_POOL_TIMEOUT_SECONDS` (10): a request waits at most this long for a free pooled connection
  and then fails, instead of queueing for SQLAlchemy's default 30 s behind everyone else.
- `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` (300000, 0 disables): Postgres ends a session that sits idle
  inside a transaction. An open transaction pins a connection and holds locks that block DDL, so a
  leaked one must not live for hours. It is long enough for a chat turn, which holds a transaction
  across the model call; it is a backstop, not a budget.

**Long-lived responses must not hold a session.** The request-scoped session (`get_session`)
commits and closes only when the response ends, and an SSE stream ends when the tab closes. So
`/updates/stream` and `/quote/stream` live on their own router (`context.stream_router`) whose
caller is resolved by `optional_user_released` on a short session that is committed and closed
before the stream starts: an open tab holds no connection and no transaction. Test:
`tests/api/test_stream_sessions.py`.

**Probes do not share the pool.** `/readyz` and `/startupz` open their own non-pooled connection
with a 3 s limit, so a replica whose application pool is exhausted still reports busy-but-alive
instead of failing readiness and being ejected (which pushes its load onto the other replicas).

**The schema lock outlasts the statement timeout.** `db/schema_lock.py` takes a transaction-level
advisory lock with `SET LOCAL statement_timeout = 0`, so processes waiting behind a slow first
`create_all` wait instead of failing startup with `QueryCanceled`, and nothing leaks back into the
pool.

**Row-level security is applied only where it differs.** With `ROW_LEVEL_SECURITY=true`, every
process start used to run four `ALTER TABLE` / `POLICY` statements on every user-owned table, and
they need exclusive locks: one session left idle in a transaction made the start-up wait (and every
later query on that table queue behind it) until the statement timeout. `db/rls.py::apply_policies`
now reads each table's state in one lock-free query and compares it with a fingerprint of the policy
definition stored as a comment on the policy. A current table gets **no DDL at all**, so a normal
restart or rolling deploy never touches a lock. A table that does differ is fixed in its own short
transaction with a 3 s `lock_timeout` and a few jittered retries; if a session still blocks it,
startup fails with `RlsPolicyBlockedError` naming the table instead of piling up queries. Tests:
`tests/db/test_rls_apply.py`.

## Outbound HTTP clients

`backend/src/netclient/` holds the shared connection pool. `sources.tls.build_async_client` (every
market-data adapter), the Paddle client, the OAuth providers, the OpenAI-compatible LLM client and
the notification delivery transport all use `async with lease_client(...) as client`. What is
pooled is the **transport** (keep-alive connections and TLS sessions, per event loop and TLS
setting), so a call no longer pays a new TCP + TLS handshake. Each lease gets its **own client** on
top of it: its own cookie jar, headers and timeout. That separation is deliberate: a shared client
would send a cookie set in answer to one caller's request on the next caller's, a cross-tenant leak
when callers are different users. Leaving the `async with` does not close the pool; the API closes
it at shutdown. Idle keep-alive connections expire after 15 s, and a failed connection attempt is
retried once (nothing was sent, so this is safe for any method).

Status-based retries are opt-in (`retry_reads`) and only for GET/HEAD: up to 3 attempts on a
connection error, timeout or a 429/502/503/504, exponential backoff with jitter, `Retry-After`
honoured up to 5 s. The Paddle client uses it; the market-data sources do not, because the ingest
rule is "a failed fetch journals FAILED and the next scheduled run retries", and some
sources are metered. The Anthropic, OpenAI, Gemini and voice SDKs manage their own HTTP clients.
