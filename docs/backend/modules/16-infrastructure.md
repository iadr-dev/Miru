# Module: Infrastructure & operations (`backend/src/`, deployment)

The hosted/distributed-systems layer. The agent design is single-process-friendly, but the
product is **multi-user hosted**, so several mechanisms specified as "in-process" need a
cross-process story before build. This module is the source of truth for that. Raised by the
Backend Architect + Database Optimizer reviews (see [../adr/0015](../adr/0015-hosted-infrastructure.md)).

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
        Scheduler workers (≥1, leader-elected)  ← background: jobs, alerts, delivery
        Broker/queue (Celery/RQ) for scraper + delivery tasks  ← not built, design intent
```

- **API workers** are stateless — any worker serves any request (including SSE reconnect),
  which is why live events must go through Redis, not an in-process bus (see §2).
- **Scheduler workers** run the §9a jobs/alerts; ≥1 with leader election (see §5).
- **Real process types today (`docker-entrypoint.sh`'s `SERVICE_ROLE` dispatch):** `api`,
  `scheduler`, `ingest`, `mcp` — one image, role-selected entrypoint, each a distinct container in
  `docker-compose.yml`. **A generic `worker(queue)` process/broker (Celery/RQ) does not exist in
  this codebase** — it is design intent for future scraper/delivery task offload, not a built
  role; scale `api` horizontally, `scheduler`/`ingest` are each single-leader-only today
  (`ingest.leader.AlwaysLeader` — no real cross-process election exists yet, see §5).

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

## 2a. Live-quote feed — shared demand-driven poller + SSE fan-out (governance §9b)

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
  (governance §9b) — bounded observation, not an unattended scan.
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
- **Payload freshness metadata (for the client `DataFreshnessBadge` + off-hours handling — [web data-freshness.md](../../web/data-freshness.md) §3):**
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
  so the "only `sources/` does I/O" invariant (ADR-0002) holds. `context/` calls
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

## 3a. Read-model freshness contract (client cache-coherence) — design intent, partially built

So a client cache can never extend a value's freshness budget ([web data-freshness.md](../../web/data-freshness.md)
— the coherence invariant), the read surface should expose the server's own currency, cheaply.
**Implementation status:** only the `data.updated` invalidation signal (the third bullet) is
real code today; `as_of` on every cacheable REST response, conditional GET, and the
`Cache-Control` policy are still design intent, not yet built.

- **`as_of` on every cacheable read — partially real.** The live-quote SSE frame
  (`events/live_quote.py`'s `quote` event) carries a real `as_of` (the snapshot's source
  timestamp, from `api/quote_stream.py`'s `_extract_as_of`) and `market_open`. **Not yet built:**
  a uniform `as_of`/`timing_class` field on the plain `GET /quote`, `/analytics/*`, `/market/*`
  REST responses — those return whatever shape the backing tool's output model defines today
  (some, like `score_tools.py`'s `as_of`, already carry a currency field; there is no codebase-
  wide convention enforcing one on every cacheable route yet).
- **Conditional GET (`ETag` / `If-None-Match`) — not yet built.** No route in `api/routers/`
  sets an `ETag` or reads `If-None-Match`; there is no `304 Not Modified` path anywhere in this
  codebase today.
- **Batch-completion invalidation signal — real.** `GET /updates/stream`
  (`api/routers/context.py`) is real: it subscribes to `AppState.data_update_broker`'s
  reader and relays each `data.updated`-shaped frame the ingest leader (or, in dev, the
  in-memory broker) publishes. The publisher/reader pair is real
  (`events/data_updates.py`, wired per backend in `events/live_quote_factory.py`); whether the
  ingest leader itself calls the publisher on every batch landing is a per-batch-writer concern
  outside this module.
- **`Cache-Control` — not yet built.** No route in `api/routers/` sets a `Cache-Control` header;
  the described `private, max-age=0, stale-while-revalidate=...` policy and the "single
  server-side per-class TTL echoed to the client" model are design intent only.
- **Entitlement-keyed reads — enforced per-call, not as an HTTP cache key.** Every tool
  dispatch already resolves entitlement fresh per call (`dispatch_tool`'s sponsor gate, §10's
  "resolved per tool call" row) — so a stale *masked* payload is never served regardless of
  caching, because there is no server-side response cache in front of these routes today. The
  "cache entry keyed by entitlement" framing describes a future response-cache layer, not
  today's uncached dispatch-per-request reality.

## 4. FinMind Sponsor quota — caching + fair-share (fixes the shared-token ceiling)

A single Sponsor token = **6000 req/hr pooled across all paid users** (~1.67/s) — a hard
ceiling. Mitigation (also [12-multi-user-tiers.md](12-multi-user-tiers.md) §4) — **none of §4's
three mitigations is built yet**; today the only real guard against the pool is the per-tier
`chat_limit_per_day`/`requests_per_minute` in `accounts/entitlements.py` (`TIER_LIMITS`), which
bounds a user's overall chat rate but implements no FinMind-specific sub-quota, cache, or
capacity model:
- **Cache-first — not yet built.** No DuckDB/Redis caching `SourceAdapter` wrapper exists in
  `sources/` today (this is the same pending seam [02-packages.md](02-packages.md) flags as
  `MarketDataStore` design intent, not current code); every Sponsor read hits FinMind directly.
- **Fair-share — not yet built.** No per-user sub-quota or priority within the pool exists;
  `finmind_class_for` (`accounts/entitlements.py`) only resolves which *token class* (free vs.
  sponsor) a user's tier uses, not a share of the sponsor pool itself.
- **Capacity model — not yet built.** No documented saturation point or multi-token/coalescing
  plan exists in code or config.

## 5. Scheduler HA & delivery semantics (fixes SPOF)

[14-scheduler-alerts.md](14-scheduler-alerts.md) runs ≥1 scheduler worker (today: exactly one —
`scheduler/__main__.py`'s single daemon process gated by `ingest.leader.LeaderElection`, whose
only real implementation is `AlwaysLeader`; **not yet built:** a real cross-process election
— Postgres advisory locks or a broker-based lease — so multi-worker-without-double-firing is
design intent, not current capacity):
- **Leader election / locking job store** — design intent above; `AlwaysLeader`/`NeverLeader`
  are the only implementations today (single-process-correct, not yet multi-worker-safe).
- **At-least-once execution + idempotent delivery — real:** `dedupe_key_for(source_id,
  trigger_date, channel)` (`scheduler/models.py`) backs the `notifications` unique dedupe guard
  `scheduler/worker.py::_deliver` checks before sending — a redelivered
  LINE/Telegram/in-app/push is a no-op.
- **Retry + DLQ — not yet built.** A channel's `DeliveryReceipt.retryable` flag is logged
  (`_log_receipt`) but nothing currently retries or routes it to a dead-letter queue.
  **`running` status — real:** `JobStatus.RUNNING` (`scheduler/models.py`) is set before a job
  fires and used to detect stuck/orphaned runs after a crash.

## 6. Concurrency, cost & abuse ceilings

- **FastAPI I/O model:** the source/tool I/O path is async end-to-end; genuinely blocking
  adapters (scrapers, some SDKs) run in a thread/process pool so they never block the event
  loop / SSE. ASGI worker count + pool sizing are config.
- **Per-turn hard budget:** beyond the concurrency cap, a **max steps + max total cost/tokens
  per turn** kill-switch (not just parallelism) bounds runaway fan-out/loops
  ([11-orchestration.md](11-orchestration.md) §5).
- **BYOK lifts only the LLM chat limit** — FinMind quota, web-search, scraper load, and
  request-rate limits still apply (platform-side cost). See [12](12-multi-user-tiers.md) §5.
- **Provider resilience — design intent, not yet built here.** No circuit breaker exists
  anywhere in `src/` for any provider or scraped source, and no generic retry-with-backoff
  wrapper exists in `llm/`. See [04-llm-adapter.md](04-llm-adapter.md) for that module's own,
  separately-reconciled account of its current (narrower) fallback behavior.

## 7. Observability (currently missing)

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
  `JWT_SECRET_KEY`/`JWT_ALGORITHM`/`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (**recommend ~15, not 1440** —
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
  sub-agent/scheduler boundary in plaintext ([governance.md](../governance.md) §6/§9), never logged.

## 9. Migrations & data lifecycle

- **Migrations:** Alembic (SQLAlchemy); every migration must apply on **both SQLite and
  Postgres** in CI (expand-contract for evolving the message-tree / `stream_events` schema).
- **Retention / partitioning:** see [../database-schema.md](../database-schema.md) — `usage_events`/
  `journal_entries`/`billing_events` time-range partitioned on Postgres (monthly; cheap detach/drop);
  **`stream_events` is TTL-purged, NOT partitioned** (partitioning would break its
  `UNIQUE(turn_id, event_seq)` guard); concrete TTLs + purge-job owner; PII scrub on account purge.

## 10. Concurrency model (the single guarantee for shared state)

API workers are stateless and horizontally scaled, so **every shared-state hot path has a
declared concurrency-safety mechanism** — no path relies on "one process at a time." Matrix:

| Contended path | Hazard | Mechanism |
|----------------|--------|-----------|
| `usage_counters` increment | lost update (parallel turns, same user/window) | atomic `INSERT … ON CONFLICT (pk) DO UPDATE SET n = n+1` ([10](10-events-hooks.md) §4) |
| Rate-limit / daily-chat gate | two concurrent turns both pass the check | check-and-increment in the **same atomic upsert** (Redis `INCR` + TTL, or the counter upsert), not read-then-write |
| Quota check (chat/day, FinMind) | TOCTOU — both admitted over the cap | reserve **before** work (atomic decrement of a Redis budget); refund on failure |
| `subscriptions` (Paddle) | out-of-order / duplicate webhooks | single writer + `occurred_at` ordering guard + `billing_events` idempotency ([15](15-payments-paddle.md)) |
| `threads` (rename/star/archive, multi-device) | lost update | optimistic `version` compare-and-set (409 on stale) |
| **Thread active-leaf / branch switch** | two devices edit/switch same thread | CAS on `threads.active_leaf_message_id` keyed by `version`; last-writer-wins with 409 retry |
| **Concurrent turns in one thread** | two `POST /agent/query` on same thread interleave | **Partially built.** The DB has the guard: a partial-unique index on `turns` (`status='running'`, applied in the migration — `db/models.py` §comment). **But no request-path code ever writes `status='running'`** — `ConversationStore.append_turn` always inserts a turn with `status=TurnStatus.DONE` (`conversation/store.py`), so the constraint is present but never exercised, and `POST /agent/query` does not check `TurnRegistry.active_for_thread` before starting a second turn — today two concurrent requests on the same thread can both proceed. |
| `messages.seq` / `stream_events.event_seq` | gap/collision across parallel sub-agent lanes | assigned in-process by the **single event-bus writer per turn**; `UNIQUE(turn_id, seq)` guard |
| Idempotent turn create | double-submit / retry | **Partially built.** The DB constraint is real (`uq_turns_user_idempotency_key`), and a separate `conversation.turn_lifecycle.TurnLifecycle` class implements the full in-process-dedupe-map + durable-constraint-backstop pattern — but `POST /agent/query` (`api/routers/agent.py`) does **not** use `TurnLifecycle`; it calls `ConversationStore.append_turn` directly, which does not check for an existing `idempotency_key` first and does not catch the constraint's `IntegrityError` — a real double-submit against the live HTTP route hits an unhandled 500, not a graceful replay. No Redis lock exists anywhere in this path. |
| Scheduler jobs | multiple workers fire same job | leader election / locking job store; at-least-once + idempotent delivery dedupe (§5) |
| Live-quote poller | duplicate polls for a symbol | single shared poller; demand set is a Redis refcount (§2a) |
| **Cache fill (market data)** | stampede — N requests miss + all fetch same key | **Not yet built** — no caching layer exists in `sources/` yet (§3/§4), so there is no single-flight-on-miss mechanism to race today; design intent is a per-key lock so concurrent misses await one fetch |
| **Market-data store (DuckDB/Parquet)** | leader writer + API-worker readers on one file → **lock error / torn read** (DuckDB: 1 read-write *or* N read-only per file; MVCC in-process only) | **Readers = read-only Parquet** (`read_parquet` glob, per-worker `:memory:` DuckDB — no shared file lock). **Writer = leader only**; partition writes are **temp-dir → atomic rename + manifest pointer** so a glob never straddles a swap; startup sweeps orphaned temp dirs. DuckDB version pinned ([22](22-data-ingest.md) §7) |
| **Watchlist reorder (item/group), multi-device drag** | two devices reorder same list → lost update / flicker | **lexorank `sort_order` (fractional key)** — insert *between* neighbors, no neighbor rewrite; equal-midpoint collision → jitter+retry; bulk reshuffle uses `POST /watchlists/reorder` full-array + parent `version` CAS ([adr/0027](../adr/0027-first-class-watchlist.md)) |
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
- Nothing here weakens governance: caching preserves currentness caveats; secrets stay
  encrypted; per-user isolation holds across cache/Redis keys (namespaced by `user_id`).
