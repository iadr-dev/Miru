1	# Module: Infrastructure & operations (`backend/src/`, deployment)
2	
3	The hosted/distributed-systems layer. The agent design is single-process-friendly, but the
4	product is **multi-user hosted**, so several mechanisms specified as "in-process" need a
5	cross-process story before build. This module is the source of truth for that. Raised by the
6	Backend Architect + Database Optimizer reviews (see [../adr/0015](../adr/0015-hosted-infrastructure.md)).
7	
8	## 1. Deployment topology
9	
10	```
11	                    ┌───────── Load balancer ─────────┐
12	                    ▼                                  ▼
13	        API workers (uvicorn, N)              API workers …
14	        - FastAPI REST + SSE                  (stateless; any worker serves any request)
15	        - agent runtime / orchestrator
16	              │            ▲ SSE fan-out
17	              ▼            │
18	        ┌───────────── Redis ─────────────┐   ← cross-process transport + hot cache + locks
19	        │ pub/sub (live event fan-out)     │
20	        │ quota counters / rate limits     │
21	        │ idempotency locks                 │
22	        │ hot cache (snapshots, health)     │
23	        └───────────────────────────────────┘
24	              │                    │
25	        PostgreSQL (app state)   DuckDB/Parquet (derived market data, columnar)
26	              │
27	        Scheduler workers (≥1, leader-elected)  ← background: jobs, alerts, delivery
28	        Broker/queue (Celery/RQ) for scraper + delivery tasks
29	```
30	
31	- **API workers** are stateless — any worker serves any request (including SSE reconnect),
32	  which is why live events must go through Redis, not an in-process bus (see §2).
33	- **Scheduler workers** run the §9a jobs/alerts; ≥1 with leader election (see §5).
34	- **Process types:** api, scheduler, worker(queue). Scale api horizontally; scheduler is
35	  1-active-leader + standbys.
36	
37	## 2. Cross-process event transport (fixes in-process bus at scale)
38	
39	The event bus ([10-events-hooks.md](10-events-hooks.md)) is in-process **within one turn's
40	worker**. But `POST /agent/query` runs a turn on worker A while `GET /threads/{id}/stream`
41	(reconnect) may hit worker B. So the **live SSE fan-out goes through Redis Pub/Sub** keyed by
42	`turn_id`:
43	
44	- The turn's worker publishes each enveloped event to `stream:{turn_id}` (Redis) **and**
45	  appends to the durable `stream_events` table.
46	- Any worker serving `GET /threads/{id}/stream` subscribes to `stream:{turn_id}` for live
47	  events, and replays missed events from `stream_events` by `event_seq` on `Last-Event-ID`.
48	- In-process bus remains a same-worker optimization; Redis is the cross-worker source of truth
49	  for the live path. `stream_events` is the durable replay log.
50	
51	## 2a. Live-quote feed — shared demand-driven poller + SSE fan-out (governance §9b)
52	
53	Premium/Pro intraday quotes (`price_intraday`) are pushed, not client-polled — the scaling-safe
54	design (1 poll → N clients instead of N clients × N polls against the 6000/hr Sponsor pool).
55	
56	```
57	client opens  GET /quote/stream?symbols=2330,0050&market=TW   (SSE; reuses §2 transport)
58	   │  subscription registered in Redis: demand{TW}: {2330, 0050, …} (refcounted per connection)
59	   ▼
60	shared quote poller (one worker):  every 5s, for each symbol with ≥1 subscriber & market-open:
61	   fetch snapshot via dataaccess router (price_intraday → finmind/twse_mis) → cache →
62	   publish to Redis  quote:{market}:{symbol}
63	   ▼
64	each API worker's SSE handler subscribes to its connection's quote:* channels → pushes `quote` events
65	   │  client disconnects → refcount--; at 0 the symbol leaves the demand set → poller stops it
66	```
67	
68	- **Transport = SSE + Redis** (the same fan-out as agent chat, §2) — a quote feed is
69	  server→client only; subscribe/unsubscribe is just the stream's `symbols` param (or a small
70	  `POST /quote/subscribe`), so no WebSocket is needed. Detail page = 1 symbol; watchlist = N;
71	  an agent `rich_card` quote piggybacks the same mechanism.
72	- **Demand set = union of all live subscriptions** (Redis refcount). The poller **only** touches
73	  symbols someone is watching, **only** in market hours, **auto-stops** on last disconnect
74	  (governance §9b) — bounded observation, not an unattended scan.
75	- **Caps:** per-connection symbol cap + global demanded-symbol cap; feeds the FinMind Sponsor
76	  quota manager (§4) — over-budget → slower cadence / cached, never a tight loop.
77	- **Cadence tiers (client hint).** The stream accepts a `cadence` hint on subscribe — `normal` (~5s,
78	  default) or **`saver`** (a slower snapshot interval, e.g. ~20–30s) for **mobile data-saver / metered
79	  connections**; the client requests `saver` on cellular/low-power and the poller serves that symbol at
80	  the coarser interval (one poll still fans out to all subscribers at that symbol's slowest requested
81	  cadence). A backgrounded mobile app **unsubscribes** entirely (refcount→0 stops the poll) and
82	  revalidates on foreground — see [mobile interaction-spec §6.1](../../mobile/interaction-spec.md). The
83	  delayed-snapshot caveat is unchanged; `saver` just widens the interval, never fabricates.
84	- **Entitlement:** `/quote/stream` intraday is `sponsor` (Premium/Pro); an unentitled client gets
85	  the masked teaser / EOD only. Payload keeps the delayed-snapshot caveat (not true real-time).
86	- **Off-hours / no subscribers:** poller idle; `/quote` (non-stream) still serves last snapshot/EOD.
87	- **Payload freshness metadata (for the client `DataFreshnessBadge` + off-hours handling):**
88	  every `quote` event carries **`as_of`** (the snapshot's source timestamp, not the publish time) and
89	  **`market_open`** (bool). The client stamps the badge from `as_of` and, once `market_open=false`,
90	  **stops treating the last tick as current** (shows last-close/EOD, static) — so a frozen quote after
91	  close never implies a still-ticking market. `as_of` is also the client's **monotonic discard
92	  validator** (drop any tick not newer than what's shown).
93	
94	## 3. Derived market-data store & cache tier (`MarketDataStore` impl)
95	
96	Market data is **not** in the app Postgres DB. It lives in a dedicated tier — the concrete
97	implementation of the **`MarketDataStore` protocol** ([02-packages.md](02-packages.md)),
98	config-selected like every other store:
99	
100	| Layer | Tech | Holds | TTL / refresh |
101	|-------|------|-------|---------------|
102	| **Columnar store** | **DuckDB over Parquet** | EOD/history, financials, holdings, the normalized `etf_holdings(etf_id,stock_id,weight,shares,date)`, canonical package versions | `official-eod` → refresh post-close; immutable canonical is versioned, never mutated |
103	| **Hot cache** | **Redis** | source-health, latest snapshots, freshly-built conversation packages, sponsor-dataset reads | short TTL by `timing_class`: `reference` sec–min; `live-ish` seconds; `official-eod` until next close |
104	
105	- **Read-through & layering:** the cache is a **caching `SourceAdapter` wrapper in `sources/`**,
106	  so the "only `sources/` does I/O" invariant (ADR-0002) holds. `context/` calls
107	  `MarketDataStore` abstractly and never opens Redis/DuckDB/sockets itself. Path: `context/` →
108	  `MarketDataStore` (Redis hit → DuckDB hit) → on miss the caching adapter fetches via the real
109	  source adapter and writes back. `get_canonical_context` is "(cached, no network)" because of
110	  this tier.
111	- **FinMind `storage_objects`** whole-day parquet (tick/minute-K/分點) lands directly in the
112	  Parquet store.
113	- **Cache key = `(capability, market, symbol[, date])`** so multi-market data (TW/US/JP…) never
114	  collides; **invalidation** keyed on canonical version + `timing_class` (live-ish never cached
115	  beyond seconds). Staleness surfaced honestly via M5Q, never hidden.
116	- **Single-flight on miss (stampede guard):** the first request to miss a key takes a per-key
117	  Redis lock and fetches; concurrent misses for the same key **await that result** instead of
118	  each hitting the source. Critical for the FinMind Sponsor pool — a hot symbol = 1 upstream
119	  fetch, not N (§10 concurrency).
120	- **Reader/writer isolation (DuckDB's one-writer-OR-many-readers rule,
121	  `duckdb.org/docs/stable/connect/concurrency`):** the **leader ingest process is the sole DuckDB
122	  writer** and emits **Hive-partitioned Parquet** (`market=…/date=…`) via an atomic
123	  temp-dir→rename + manifest swap ([22](22-data-ingest.md) §4). **API workers are read-only**
124	  consumers that open the **Parquet directly** (per-worker in-memory DuckDB,
125	  `read_parquet(glob, hive_partitioning=true)`) — **never the writer's `.duckdb` file** (opening a
126	  file the leader holds read-write throws a lock error; the two modes are mutually exclusive per
127	  file). Redis fronts **point** reads only; the **analytical screener/scoring SQL always executes
128	  against Parquet** — it is not a cacheable key, so "Redis makes read-concurrency moot" does **not**
129	  hold. Read-only Parquet is what scales the reader fan-out ([22](22-data-ingest.md) §7).
130	
131	## 3a. Read-model freshness contract (client cache-coherence)
132	
133	So a client cache can never extend a value's freshness budget
134	(the coherence invariant), the read surface exposes the server's own currency, cheaply:
135	
136	- **`as_of` on every cacheable read.** Each `GET /quote`, `/analytics/*`, `/market/*` response
137	  carries an **`as_of`** timestamp = when the served value was produced (ingest/compute time), plus
138	  the `timing_class` it belongs to. This is the client's **monotonic validator** — it discards any
139	  response not newer than what it holds, and stamps the `DataFreshnessBadge`. `as_of` reflects the
140	  cache key's canonical version (§3), not the HTTP hit time.
141	- **Conditional GET (`ETag` / `If-None-Match`).** Cacheable reads return a strong **`ETag`** derived
142	  from the canonical version; a client revalidate sends `If-None-Match` → **`304 Not Modified`** (no
143	  body, no re-render) when nothing changed. This makes boundary revalidation *cheap* — the client
144	  polls the boundary but only pays for a payload when the value actually advanced.
145	- **Batch-completion invalidation signal.** When a derived-compute batch lands
146	  (`compute-scores` / `compute-topN` / `compute-ai-summary` / `discover-themes`, [22](22-data-ingest.md)
147	  §2), the ingest leader publishes a lightweight **`data.updated`** event over the §2 transport
148	  carrying `{capability, market, as_of, symbols?|scope}`. API workers relay it on the client's SSE
149	  connection so the frontend **invalidates exactly the affected keys** (`revalidateTag`) the moment
150	  fresh analytics exist — instead of waiting out a time boundary. Absent a subscription, the
151	  boundary-anchored `revalidate` (fixed batch time) remains the correct floor.
152	- **`Cache-Control`.** Cacheable reads send `private, max-age=0, stale-while-revalidate=<to class
153	  boundary>` — the browser serves the last good value instantly and background-revalidates; the
154	  intraday stream is `no-store` (it's live SSE, not a cacheable body). The **single per-class TTL**
155	  lives server-side and is echoed to the client so RSC/TanStack/HTTP layers mirror one budget, never
156	  sum three (the client-side rule).
157	- **Entitlement-keyed reads.** A sponsor-gated read's cache entry is keyed by entitlement so a tier
158	  change busts the cached **masked** payload (per-call entitlement, §10) — an upgraded user never
159	  keeps seeing the 馬賽克 cell.
160	
161	## 4. FinMind Sponsor quota — caching + fair-share (fixes the shared-token ceiling)
162	
163	A single Sponsor token = **6000 req/hr pooled across all paid users** (~1.67/s) — a hard
164	ceiling. Mitigation (also [12-multi-user-tiers.md](12-multi-user-tiers.md) §4):
165	- **Cache-first:** Sponsor EOD data (分點/官股/產業鏈) is T+1 and changes once/day → cache per
166	  `(dataset, stock, date)` in DuckDB/Redis; repeat reads never re-hit FinMind. The vast
167	  majority of the 6000 budget should be cache misses only.
168	- **Fair-share: not yet built.** The real reserver (`RedisFinMindPoolReserver`,
169	  `usage/finmind_pool.py`) is a purely **global** rolling-window counter — its atomic
170	  `INCRBY`/`DECRBY` reservation is keyed on `(key_prefix, window bucket)` only, with no `user_id`
171	  dimension, no per-user sub-quota, no priority, and no queuing. One heavy user's fan-out can
172	  exhaust the whole pool for every other paid user today; only the global reserve-before-act
173	  ceiling (grant up to headroom, refund the overage) is enforced.
170	- **Capacity model:** document the paid-user count at which one token saturates and the plan
171	  (multiple Sponsor tokens, request coalescing). The ceiling must be visible, not discovered
172	  in production.
173	
174	## 5. Scheduler HA & delivery semantics (fixes SPOF)
175	
176	[14-scheduler-alerts.md](14-scheduler-alerts.md) runs ≥1 scheduler worker:
177	- **Leader election / locking job store** (Postgres advisory locks or a broker) so multiple
178	  workers run safely without double-firing; no SPOF.
179	- **At-least-once execution + idempotent delivery:** dedupe key `(rule_id/job_id, trigger_date,
180	  channel)` in `notifications`; a redelivered LINE/Telegram/web native is a no-op.
181	- **Retry + DLQ: designed but not yet implemented.** `scheduler/` has no retry-queue or
182	  dead-letter logic today — grepped the whole package, none exists. `DeliveryReceipt.retryable`
183	  (`scheduler/delivery/base.py`) is a boolean field nothing currently consumes; the field's own
184	  docstring frames `retryable=True` as a documented future seam, not implemented behavior.
185	  `scheduler/worker.py::_log_receipt` just logs the receipt (channel, `delivered`, `retryable`) —
186	  a transient channel failure is observed, never retried or routed to a dead-letter path.
187	  **`running` status** is added to `schedule_jobs` to detect stuck/orphaned runs after a crash.
183	
184	## 6. Concurrency, cost & abuse ceilings
185	
186	- **FastAPI I/O model:** the source/tool I/O path is async end-to-end; genuinely blocking
187	  adapters (scrapers, some SDKs) run in a thread/process pool so they never block the event
188	  loop / SSE. ASGI worker count + pool sizing are config.
189	- **Per-turn hard budget:** beyond the concurrency cap, a **max steps + max total cost/tokens
190	  per turn** kill-switch (not just parallelism) bounds runaway fan-out/loops
191	  ([11-orchestration.md](11-orchestration.md) §5).
192	- **BYOK lifts only the LLM chat limit** — FinMind quota, web-search, scraper load, and
193	  request-rate limits still apply (platform-side cost). See [12](12-multi-user-tiers.md) §5.
194	- **Provider resilience:** per-call timeout budgets, retry-with-backoff, error classification
195	  (rate-limit/5xx/timeout → fallback vs fail), and a **circuit breaker** per provider and per
196	  scraped source ([04](04-llm-adapter.md) fallback chains).
197	
198	## 7. Observability (currently missing)
199	
200	- **Distributed tracing** across API → orchestrator → sub-agents → tools → sources → LLM,
201	  using the SSE **correlation envelope (`turn_id`/`agent_id`/`node_id`/`seq`) as the trace id**.
202	- **Structured logs** with those correlation IDs; **SLIs/SLOs**: turn latency, SSE
203	  time-to-first-token, tool error rate, provider latency, FinMind pool utilization,
204	  scraper-parse success rate.
205	- **Scraper-drift monitoring** (MOPS/PTT/ETF-PCF/Brave): detect silent parse corruption
206	  (parser returns empty/degenerate) — distinct from M5Q staleness — with alerting + versioned
207	  parsers + canaries. Treat scraped sources as a monitored subsystem.
208	- **Dashboards + alerting** on the above; per-skill quality already covered by feedback ([13](13-chat-interaction-features.md)).
209	
210	## 8. Secrets & auth config
211	
212	- **OAuth (sign-in):** `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/`GOOGLE_REDIRECT_URI`;
213	  `LINE_CHANNEL_ID`/`LINE_CHANNEL_SECRET`/`LINE_REDIRECT_URI` (LINE **Login** channel). PKCE
214	  `code_verifier` + `state` are per-request (Redis/`oauth_flows`, not env). **JWT session:**
215	  `JWT_SECRET_KEY`/`JWT_ALGORITHM`/`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (**recommend ~15, not 1440** —
216	  access token is in-memory + refreshed; short expiry makes revocation fast)/`JWT_REFRESH_TOKEN_EXPIRE_DAYS`.
217	  Storage/rotation model: [modules/12](12-multi-user-tiers.md) §1b.
218	- **Notification channels (separate from Login):** `LINE_MESSAGING_*` (Messaging API — the
219	  `DeliveryChannel`, distinct channel from LINE Login), `TELEGRAM_BOT_TOKEN`/`_WEBHOOK_SECRET`.
220	- **Data providers:** `FINMIND_API_KEY`, `FINNHUB_KEY`, `BRAVE_SEARCH_API_KEY`. **Reserved for
221	  phased US/macro adapters** (not yet specified in the source inventory — [architecture §3](../architecture.md)):
222	  `ALPHA_VANTAGE_API_KEY`, `FRED_KEY`, `FMP_API_KEY` — wire only when their adapters land ([19](19-data-access-router.md) phased markets).
223	- **LLM:** the 7-provider keys ([04](04-llm-adapter.md)); `PADDLE_*` billing.
224	- **Handling:** all above are **KMS envelope-encrypted** platform secrets (not a static app
225	  key); documented key-rotation; DB-at-rest is separate from field-level (`user_secrets.ciphertext`
226	  for BYOK/per-user). Secrets decrypt **in-process at call time only**, never cross the
227	  sub-agent/scheduler boundary in plaintext ([governance.md](../governance.md) §6/§9), never logged.
228	
229	## 9. Migrations & data lifecycle
230	
231	- **Migrations:** none yet, by design (development stage): `create_all()` + the additive upgrade keep the schema current and a destructive change rebuilds the dev database. **Alembic is adopted at the first production release**; from then every migration must apply on **both SQLite and
232	  Postgres** in CI (expand-contract for evolving the message-tree / `stream_events` schema).
233	- **Retention / partitioning:** see [../database-schema.md](../database-schema.md) — `usage_events`/
234	  `journal_entries`/`billing_events` time-range partitioned on Postgres (monthly; cheap detach/drop);
235	  **`stream_events` is TTL-purged, NOT partitioned** (partitioning would break its
236	  `UNIQUE(turn_id, event_seq)` guard); concrete TTLs + purge-job owner; PII scrub on account purge.
237	
238	## 10. Concurrency model (the single guarantee for shared state)
239	
240	API workers are stateless and horizontally scaled, so **every shared-state hot path has a
241	declared concurrency-safety mechanism** — no path relies on "one process at a time." Matrix:
242	
243	| Contended path | Hazard | Mechanism |
244	|----------------|--------|-----------|
245	| `usage_counters` increment | lost update (parallel turns, same user/window) | atomic `INSERT … ON CONFLICT (pk) DO UPDATE SET n = n+1` ([10](10-events-hooks.md) §4) |
246	| Rate-limit / daily-chat gate | two concurrent turns both pass the check | check-and-increment in the **same atomic upsert** (Redis `INCR` + TTL, or the counter upsert), not read-then-write |
247	| Quota check (chat/day, FinMind) | TOCTOU — both admitted over the cap | reserve **before** work (atomic decrement of a Redis budget); refund on failure. Reservation fires from two `orchestrator.py` call sites: `_start_fanout` (the `subagent`/`team` frameworks, via `_run_subagent`/`_run_team`) reserves per fan-out lane, and — closing a scope gap that previously left it unreserved — `_run_single` also reserves directly whenever its resolved skill's tool allowlist includes a Sponsor-tier tool (`_has_sponsor_tool`), stripping those tools from the loop if the reservation comes back degraded |
248	| `subscriptions` (Paddle) | out-of-order / duplicate webhooks | single writer + `occurred_at` ordering guard + `billing_events` idempotency ([15](15-payments-paddle.md)) |
249	| `threads` (rename/star/archive, multi-device) | lost update | optimistic `version` compare-and-set (409 on stale) |
250	| **Thread active-leaf / branch switch** | two devices edit/switch same thread | CAS on `threads.active_leaf_message_id` keyed by `version`; last-writer-wins with 409 retry |
251	| **Concurrent turns in one thread** | two `POST /agent/query` on same thread interleave | **Partially built.** The DB has the guard: a partial-unique index on `turns` (`status='running'`, `uq_turns_one_running_per_thread` in `db/models.py`). **But no request-path code ever writes `status='running'`** — `ConversationStore.append_turn` always inserts a turn with `status=TurnStatus.DONE` (`conversation/store.py`), so the constraint is present but never exercised, and `POST /agent/query` does not check `TurnRegistry.active_for_thread` before starting a second turn — today two concurrent requests on the same thread can both proceed. |
252	| `messages.seq` / `stream_events.event_seq` | gap/collision across parallel sub-agent lanes | assigned in-process by the **single event-bus writer per turn**; `UNIQUE(turn_id, seq)` guard |
253	| Idempotent turn create | double-submit / retry | **Partially built.** The DB constraint is real (`uq_turns_user_idempotency_key`), and a separate `conversation.turn_lifecycle.TurnLifecycle` class implements the full in-process-dedupe-map + durable-constraint-backstop pattern — but `POST /agent/query` (`api/routers/agent.py`) does **not** use `TurnLifecycle`; it calls `ConversationStore.append_turn` directly, which does not check for an existing `idempotency_key` first and does not catch the constraint's `IntegrityError` — a real double-submit against the live HTTP route hits an unhandled 500, not a graceful replay. No Redis lock exists anywhere in this path. |
254	| Scheduler jobs | multiple workers fire same job | leader election / locking job store; at-least-once + idempotent delivery dedupe (§5) |
255	| Live-quote poller | duplicate polls for a symbol | **per-worker** poller (not single-shared); demand set is a Redis refcount enforcing the fleet-wide cap (§2a) |
256	| **Cache fill (market data)** | stampede — N requests miss + all fetch same key | **Built.** `CachingSourceAdapter` (`sources/caching_adapter.py`) is a real read-through Redis wrapper — single-flight-on-miss via `SET NX EX` lock + poll-wait, compare-then-delete release (token-based) so a stale holder can't release another waiter's lock. Enabled by default in the real deployment (`docker-compose.yml`'s `MARKET_DATA_CACHE_BACKEND: ${MARKET_DATA_CACHE_BACKEND:-redis}`); wired in `dataaccess/composition.py::build_default_adapters` whenever `Settings.market_data_cache_backend == "redis"`. Does not cover the ingest leader's own whole-market pulls, which construct an unwrapped adapter directly (§3) — this cache tier is for the per-symbol request path only |
257	| **Market-data store (DuckDB/Parquet)** | leader writer + API-worker readers on one file → **lock error / torn read** (DuckDB: 1 read-write *or* N read-only per file; MVCC in-process only) | **Readers = read-only Parquet** (`read_parquet` glob, per-worker `:memory:` DuckDB — no shared file lock). **Writer = leader only**; partition writes are **temp-dir → atomic rename + manifest pointer** so a glob never straddles a swap; startup sweeps orphaned temp dirs. DuckDB version pinned ([22](22-data-ingest.md) §7) |
258	| **Watchlist reorder (item/group), multi-device drag** | two devices reorder same list → lost update / flicker | **lexorank `sort_order` (fractional key)** — insert *between* neighbors, no neighbor rewrite; equal-midpoint collision → jitter+retry; bulk reshuffle uses `POST /watchlists/reorder` full-array + parent `version` CAS ([adr/0027](../adr/0027-first-class-watchlist.md)) |
259	| **Watchlist group rename, multi-device** | lost update | optimistic `version` CAS (409 on stale), as `threads` |
260	| **Watchlist star toggle (add/remove item)** | double-tap / retry → dup or add↔remove race | `PUT`=`INSERT … ON CONFLICT (watchlist_id, symbol) DO NOTHING`; `DELETE` idempotent; partial-unique `(watchlist_id, symbol)` is the guarantee |
261	| **Offline action queue (mobile)** | app offline → user stars/reorders/sets alerts → replayed on reconnect out-of-order or twice | each queued action carries a **client `op_id`** (idempotency key) + the entity `version` it was based on; on reconnect the client replays **in order**, server dedupes by `op_id` and applies the existing per-entity guard (star `ON CONFLICT DO NOTHING`, reorder/rename `version` CAS → **409 surfaces as a per-op conflict** the client re-reads, not a silent overwrite). No new server primitive — it composes the existing idempotency + CAS mechanisms; the queue lives client-side ([mobile interaction-spec §6](../../mobile/interaction-spec.md)) |
262	| SSE fan-out across workers | event delivered to wrong/no worker | Redis pub/sub keyed by `turn_id`; `event_seq` replay (§2) |
263	| **Entitlement change mid-turn** | tier downgrades while a turn runs | entitlement resolved **per tool call** (pre_tool hook), not cached for the turn — a mid-turn downgrade masks the next `sponsor` call, not retroactively |
264	| **BYOK key edited mid-request** | key removed while in use | key read at call time; a removed key fails that call gracefully (fallback/limit), never a torn read |
265	| Steer vs cancel vs running turn | user steers/cancels while a step runs | steering queued, applied at next `step_start`; cancel sets a flag checked between steps; in-flight read-only tool finishes ([07](07-conversation-streaming.md)) |
266	
267	**Principles:** (1) prefer **atomic DB/Redis ops** (`ON CONFLICT`, `INCR`, CAS) over
268	read-modify-write; (2) **reserve-before-act** for budgets/quotas (avoid TOCTOU); (3) **single
269	writer** where ordering matters (event_seq, subscriptions); (4) **idempotency keys** on every
270	externally-retryable mutation (turns, webhooks, deliveries); (5) **optimistic `version`** for
271	user-facing multi-device edits, surfacing 409 for client retry. SQLite (dev) is single-writer so
272	these are latent there; they are load-bearing on Postgres/Redis in prod. Covered by
273	concurrency/load tests ([17](17-devops-operations.md) §1).
274	
275	## Governance obligations
276	- Nothing here weakens governance: caching preserves currentness caveats; secrets stay
277	  encrypted; per-user isolation holds across cache/Redis keys (namespaced by `user_id`).
278	