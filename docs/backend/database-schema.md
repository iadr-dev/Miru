# Database Schema

Authoritative schema for the hosted product. Dev default **SQLite**, production
**PostgreSQL**; access via `backend/src/db/` (ORM models + migrations) behind store
protocols (`ConversationStore`, `MemoryStore`, etc.). Every stateful table is scoped by
`user_id` for per-user isolation ([governance.md](governance.md) §9).

**Conventions:** `id` = UUID (text) PK; timestamps UTC ISO-8601; `created_at`/`updated_at`
on mutable rows; soft-delete via `deleted_at` where noted; FKs `ON DELETE CASCADE` from
`users` and `threads` unless stated. Market data is **not** stored here (it lives in the
canonical/context store + source adapters) — this schema is app/product state only.

---

## 1. Accounts & entitlements

### `users`
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| email | text **unique, nullable** | LINE users may lack email (scope declined/unapproved) → identity keyed on `oauth_accounts`, not email; partial-unique where not null |
| display_name | text | |
| picture_url | text | |
| birthday | date null | |
| phone_country_code | text null | e.g. `+886` |
| phone_number | text null | national number, digits only |
| nationality | text null | ISO 3166-1 alpha-2 (e.g. `TW`) |
| tier | text | `free` \| `premium` \| `pro` — **derived cache**, written only by the billing webhook from the active subscription's price_id (not an independent truth) |
| personality_id | text default `steady` | agent-voice preset ([modules/23](modules/23-agent-personality.md)); CHECK against the preset registry ids; user-editable via `PATCH /me` |
| created_at, updated_at | timestamptz | |
| deleted_at | timestamptz null | soft-delete |

Profile PII (birthday/phone/nationality) is user-editable via `PATCH /me`, subject to the
same per-user isolation and privacy rules; phone stored as country code + national number
separately for validation/formatting. These are **plaintext** columns — on account purge they are
**overwritten/NULLed**, not crypto-shredded (crypto-shred applies only to encrypted
`user_secrets.ciphertext`). They must be enumerated in the RLS-protected column set + the
account-purge scrub list (§Migrations & retention).

### `oauth_accounts` (multiple providers per user — enables account linking)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| user_id | uuid FK→users | **many rows per user** — one per linked provider |
| provider | text | `google` \| `line` (extensible) |
| provider_account_id | text | provider's stable subject/user id |
| email_at_provider | text null | email as seen from this provider (may differ / be null for LINE) |
| linked_at | timestamptz | |
| unique | (provider, provider_account_id) | one provider identity → one user |
| index | `(user_id)` | list a user's linked providers (`GET /me/connections`, [modules/12](modules/12-multi-user-tiers.md) §1a) |
- A user can have **both** Google + LINE linked → **one account**. Identity resolution +
  linking rules: see [modules/12](modules/12-multi-user-tiers.md) §1a.

### `user_secrets` (BYOK & channel tokens — **encrypted at rest**)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| user_id | uuid FK→users | |
| kind | text | `llm_key` \| `finmind_token` \| `channel_token` (+ `asr_key`/`tts_key` only if voice becomes BYOK; platform voice keys are env/KMS, not here) |
| provider | text | e.g. `anthropic`, `openai`, `line` |
| ciphertext | bytea | never logged/returned in plaintext |
| created_at, updated_at | timestamptz | `updated_at` for key rotation auditing (`POST /me/keys` overwrites, [modules/12](modules/12-multi-user-tiers.md) §5) |
| unique | (user_id, kind, provider) | |

### `sessions` (refresh-token store — rotation + reuse detection)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| user_id | uuid FK→users | |
| token_hash | text **UNIQUE** | hash of the current refresh token (never store raw) |
| family_id | uuid | rotation lineage; **reuse of a rotated-out token → revoke whole family** |
| rotated_from | uuid null | prior session row in the family |
| revoked_at | timestamptz null | logout / sign-out-all / reuse-revocation |
| user_agent, ip | text null | for "sign out all devices" UX |
| expires_at | timestamptz | ~30d refresh lifetime |
| created_at | timestamptz | |
| index | `(user_id)`, `(family_id)`, `(expires_at)` sweep | |
- Refresh token lives in an `httpOnly; Secure; SameSite=Lax` cookie (path `/auth/refresh`); the
  short-lived access token is **in-memory** only (see [modules/12](modules/12-multi-user-tiers.md) §1b).

### OAuth flow state (transient — **Redis, not a DB table**)
Pending-authorization state (`state` CSRF token, PKCE `code_verifier`, LINE `nonce`,
`redirect_after`) is **short-lived (~10 min TTL) and single-use**, so it lives in **Redis**
(`oauth_flow:{state}` → payload, TTL-expired, deleted on callback) — not the app DB. No
`oauth_flows` table ([modules/16](modules/16-infrastructure.md) §8). *(Dev without Redis may use an
in-process TTL map; never a durable table.)*

### `subscriptions` (Paddle — see [modules/15](modules/15-payments-paddle.md))
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| user_id | uuid FK→users | CASCADE |
| paddle_subscription_id | text unique | |
| paddle_customer_id | text null | |
| price_id | text | one of the 8 Paddle price IDs |
| tier | text | CHECK `IN ('free','premium','pro')` (derived from price_id) |
| byok | bool default false | derived from price_id (BYOK variant → lower price); no separate `interval` column — `monthly`/`annual` is derived from `price_id` at read time via `payments/plans.py`, not stored redundantly |
| status | text | CHECK `IN ('active','trialing','past_due','paused','canceled')` |
| current_period_start, current_period_end | timestamptz null | period anchors |
| scheduled_cancel_at | timestamptz null | non-null = user asked to cancel, effective at that date; tier stays active until the real `subscription.canceled` event lands |
| updated_from_event_at | timestamptz | last-applied event `occurred_at` — **order guard** (apply only if newer) |
| created_at, updated_at | timestamptz | |
| index | `(user_id)` | entitlement resolve |
- Written **only by the webhook** (single writer → no optimistic lock needed; `updated_from_event_at`
  guards ordering, not concurrency). `users.tier` is a derived cache of the active row's price_id.

### `billing_events` (Paddle webhook idempotency + audit)
| id | paddle_event_id text unique | event_type | occurred_at | processed_at | raw_payload json |
- No `user_id` column — the row is the redelivery-idempotency ledger keyed on
  `paddle_event_id` alone (a second insert of the same event collides); `raw_payload` is the
  verified envelope kept for audit (no card data, Paddle is merchant of record).

*(No local `invoices` table — Paddle (MoR) owns tax-compliant invoices/PDFs; `/billing/history`
proxies Paddle's transactions API / deep-links the portal. See [modules/15](modules/15-payments-paddle.md) §5.)*

---

## 2. Conversations

### `threads`
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| user_id | uuid FK→users | |
| title | text | auto-generated, user-overridable |
| starred | bool default false | pin |
| archived | bool default false | |
| tags | json array (portable) / PG `text[]` behind store | array types are Postgres-only — see portability |
| model_override | text null | per-chat model (entitlement-checked) |
| personality_override | text null | per-thread agent-voice preset ([modules/23](modules/23-agent-personality.md)); overrides `users.personality_id` for this thread |
| active_leaf_message_id | uuid null | pointer to the active-path leaf (updated on `switch_branch`) — avoids per-turn tree recursion |
| draft_content | text null | unsent input, survives reload ([modules/13](modules/13-chat-interaction-features.md) §3) |
| last_message_at | timestamptz | for sort |
| version | int | optimistic lock (multi-device rename/star/archive) |
| created_at, updated_at | timestamptz | |
| deleted_at | timestamptz null | soft-delete → purge (PII scrub on purge) |
| index | `(user_id, last_message_at DESC)`, `(user_id, created_at DESC)`; partial `WHERE archived=false AND deleted_at IS NULL` (default list) and `WHERE starred=true` | |
| FTS | title trigram/tsvector; **message-content FTS is a separate `message_search` artifact** (see §Search below) | |

### `turns`
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| thread_id | uuid FK→threads | |
| user_id | uuid FK→users | denormalized for isolation queries |
| role | text | `user` \| `assistant` |
| status | text | `running`\|`done`\|`cancelled`\|`error` |
| parent_turn_id | uuid null | turn lineage; branching is on `messages.parent_id` (edit-resend → switch_branch) |
| regenerate_of | uuid null | regenerate sibling group |
| framework | text null | `none`\|`single`\|`subagent`\|`team` |
| idempotency_key | text null | dedupe resend |
| created_at | timestamptz | |
| index | `(thread_id, created_at)`, `(user_id, created_at)` (denorm scoped scans), unique `(user_id, idempotency_key)` where not null | |
| **unique** | partial `(thread_id) WHERE status='running'` — **one active turn per thread** (concurrent `/agent/query` on same thread → 409/queue; see [modules/16](modules/16-infrastructure.md) §10) | |

### `messages` (content blocks — a tree via parent_id)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| turn_id | uuid FK→turns | |
| user_id | uuid FK→users | **denormalized for isolation** (the `WHERE user_id=?` wrapper + RLS need it at message grain — `message_search` already keys on it); CASCADE |
| parent_id | uuid null | message tree (branching); active path = one root→leaf chain |
| seq | int | order within turn |
| type | text | `text`\|`thinking`\|`tool_use`\|`tool_result`\|`caveat`\|`rich_card`\|`steering`\|`branch_summary`\|`compaction` |
| content | json | block payload (no raw source payloads); `branch_summary`/`compaction` hold generated summaries |
| created_at | timestamptz | |
| index | (turn_id, seq), (parent_id), (user_id) | |

### `journal_entries` (auditable tool log — **design only, not built**; see [modules/05](modules/05-agent-runtime.md))

**Not built.** `agent/journal.py` journals every Step's tool calls + guardrail verdicts as
**structured logs (structlog)**, not a DB table — its own module docstring calls a queryable
per-user/per-thread journal store "an interaction-layer concern, not yet built" and notes the
`JournalEntry` shape is deliberately DB-sink-ready for when that lands. No `journal_entries`
table exists in `backend/src/db/models.py` today. The shape below is the design.
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| turn_id | uuid FK→turns | |
| user_id | uuid FK→users | |
| kind | text | `tool_call`\|`tool_result`\|`route`\|`skill` |
| tool_name | text null | |
| data_class | text null | `public` \| `sponsor` |
| locked | bool default false | true = sponsor data masked (unentitled user) |
| skill_name, skill_version | text null | provenance |
| args_summary | json | redacted; no secrets/raw payloads |
| result_summary | json | |
| source_health | json null | currentness at time of call |
| created_at | timestamptz | |
| index | (turn_id), (user_id, created_at) | |

### `stream_events` (SSE replay for resumable streams — [modules/07](modules/07-conversation-streaming.md))
| id | turn_id FK | user_id FK→users | event_seq (monotonic) | event_type | agent_id | parent_agent_id | node_id | data json | created_at |
- **`user_id`** denormalized (CASCADE) so the isolation wrapper + RLS scope by user without a
  turn→thread join, and per-user purge is cheap.
- `agent_id`/`parent_agent_id`/`node_id` are the correlation envelope — a single ordered
  replay by `event_seq` rebuilds the full **parallel-lane timeline** deterministically after a
  reconnect (`Last-Event-ID`).
- **`UNIQUE (turn_id, event_seq)`** — gap-free monotonic seq is assigned in-process by the
  turn's single event-bus writer (the bus is source of truth); DB just persists + guards.
- **Retention: NOT time-partitioned.** Keeping `UNIQUE(turn_id, event_seq)` intact matters more than
  partitioning (Postgres would force the partition key `created_at` into that constraint, defeating
  the seq guard). A turn's events share a narrow time window, so a plain **TTL purge job** (delete
  where `created_at < now() − N`, e.g. 30–90d, kept longer than turn completion for mobile reconnect)
  is sufficient; the single event-bus writer guarantees seq order regardless.

---

## 3. Memory ([modules/09](modules/09-memory.md))

### `memories` (long-term)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| user_id | uuid FK→users | |
| scope | text | `user` \| `thread` |
| thread_id | uuid null | when scope=thread |
| type | text | `watchlist`\|`preference`\|`finding`\|`entity_note` |
| key | text | e.g. watchlist name |
| content | json | |
| source_ref | uuid null | journal/turn provenance |
| created_at, updated_at | timestamptz | |
| unique | (user_id, scope, type, key) | upsert target |

- index `UNIQUE (user_id, scope, type, key)` (upsert) ✓; add `(user_id, thread_id) WHERE scope='thread'`.

**Watchlists:** now a **first-class relational store** (§3a) — the rich right-side rail (named
groups, drag-reorder, TW/US/ETF tabs, star toggle, reverse query) outgrew a single JSON blob.
`memories(type='watchlist')` is **retired as a writer** (the `type` CHECK value is kept for legacy
rows) and replaced by a **read-through projection** for agent recall: `WatchlistStore.snapshot()`
renders the same compact `name → [symbols]` block the memory used, so agent + UI see **one** list,
no dual-write. Reverses the earlier "not shipped" decision → [adr/0027](adr/0027-first-class-watchlist.md).

---

## 3a. Watchlists ([modules/09](modules/09-memory.md), [adr/0027](adr/0027-first-class-watchlist.md))

### `watchlists` (groups / sections)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| user_id | uuid FK→users | CASCADE; every query scoped by this |
| name | text | e.g. `半導體`; per-user + tab unique (below) |
| market | text | CHECK `IN ('TW','US')` — the **true market** ([modules/19](modules/19-data-access-router.md) `Market`); no longer polluted by ETF. **Now stale vs. the real `schemas.enums.Market` enum**, which also has `GB`/`JP`/`EU`/`GLOBAL` (all real, adapter-backed since the yfinance/FinMind-international expansion, [module 01](modules/01-sources.md)) — this CHECK predates that expansion (ADR-0027) and would reject a GB/JP/EU watchlist row today |
| asset_class | text | CHECK `IN ('stock','etf')` — **instrument type**; the rail's "ETF tab" = `market='TW' AND asset_class='etf'` (resolved ADR-0027 A1) |
| sort_order | text | **lexorank/fractional key** (not int) — collision-free reorder |
| is_default | bool default false | auto-created group; ≤1 true per (user, market, asset_class) |
| version | int default 0 | optimistic lock (rename/reorder, multi-device) |
| created_at, updated_at, deleted_at | timestamptz | soft-delete |
| index | `(user_id, market, asset_class, sort_order)`; partial-unique `(user_id, market, asset_class, name) WHERE deleted_at IS NULL` | |

The rail's three tabs map to: **TW** = `(TW, stock)`, **ETF** = `(TW, etf)`, **US** = `(US, *)`.

### `watchlist_items`
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| watchlist_id | uuid FK→watchlists | CASCADE |
| user_id | uuid FK→users | CASCADE; denormalized for isolation + reverse query |
| symbol | text | canonical market-scoped id ([modules/19](modules/19-data-access-router.md) `Symbol.id`) |
| market | text | CHECK `IN ('TW','US')`; denormalized from parent (kept consistent in the add tx) → lets `/quote` batch by market without a join. Same stale-vs.-`Market`-enum gap as `watchlists.market` above (§3a note) |
| asset_class | text | CHECK `IN ('stock','etf')`; denormalized from parent |
| note | text null | optional; CHECK length ≤ 280 |
| sort_order | text | lexorank within the group |
| added_at | timestamptz | |
| index | `(watchlist_id, sort_order)`; **partial-unique `(watchlist_id, symbol)`** (idempotent star); **`(user_id, symbol)`** (reverse query — the star lit/unlit state everywhere) | |

- **No `version` on items** — point ops (add/remove/note/reorder) guarded by the unique constraint
  + lexorank, not whole-row lost-update. **Batch quotes reuse `/quote(/stream)?symbols=`** — no
  watchlist-specific quote endpoint. Ordering primitive = **lexorank** (see §Concurrency).
- **Tier soft-caps** (storage guard, not monetization — watchlist stays free): Free ~5 groups × 50
  items; Premium/Pro ~20 × 200 (`accounts/entitlements.watchlist_limit_for`, [modules/12](modules/12-multi-user-tiers.md)).

---

## 3b. Saved screens ([modules/20](modules/20-analytics-scoring.md) §5, [api-reference §4a](api-reference.md)) — **design only, not built**

**Not built.** No `saved_screens` table exists in `backend/src/db/models.py`, and the
`GET/POST/PATCH/DELETE /analytics/screener/screens` routes it would back are not registered
either — only `run_tag_screener`/`get_tag_catalog` (the real, shipped tag-flag screener, see
[api-reference.md](api-reference.md) §4a's implementation-status note) exist today. The table
shape below is the design, kept for when the custom AND/OR screener + saved-screen persistence
lands.

### `saved_screens` (a user's named screener definitions — `GET/POST/PATCH/DELETE /analytics/screener/screens`)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| user_id | uuid FK→users | CASCADE; every query scoped by this |
| name | text | user label; CHECK length ≤ 40 |
| market | text | CHECK `IN ('TW','US','ETF')` — the screener market tab the definition targets |
| conditions | json | the custom AND/OR condition list `[{factor, op, value}]` |
| match | text | CHECK `IN ('AND','OR')` |
| gate_overrides | json null | stage-A gate tuning |
| created_at, updated_at | timestamptz | |
| unique | `(user_id, market, name)` | rename/list per market |
| index | `(user_id)` | list mine |

- Screen *definitions* only (a filtered-list recipe, non-trading — [modules/20](modules/20-analytics-scoring.md) §7);
  results are computed on demand by `run_screener`, never materialized here. A shared screen is a
  read-only URL-encoded reconstruction of `{market, conditions, match}`, not a row grant.

---

## 3c. Themes, tiers & supply-chain ([modules/20](modules/20-analytics-scoring.md))

Platform **reference data**, not per-user state — these tables carry **no `user_id`** (a
stock's industry membership is the same for everyone), so the `WHERE user_id = ?` isolation
wrapper does not apply. **Layers 1 and 2 are built and populated:** `themes` + `theme_memberships`
from the FinMind industry-chain classification roster, and `theme_tiers` from a curated editorial
map plus a keyword-heuristic backfill — both on the same monthly ingest cadence, the tier seed
running immediately after the membership seed in each pass. **Layer 3 is built and
operator-triggered:** `supply_chain_edges` is populated (a hand-seeded bootstrap set with
`source='seed'`, extended by an in-theme-only LLM extractor with `source='llm_extraction'`), and
`concept`-kind `themes`/`theme_memberships` are discovered by the concept-research pipeline
(`source='concept_research'`). `theme_change_log` (added in its own migration) audits every write.
The concept-research and edge-generation writes run only when an operator invokes `python -m
analytics.themes` — never on a schedule — and every model-proposed symbol is filtered against the
stored universe before persistence (the fabrication safeguard; see modules/20-analytics-scoring.md).

### `themes` (one industry classification, or a future research/LLM concept)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | app-generated |
| key | text unique | natural slug; for industry themes the industry name itself (unique in the roster) |
| name_zh | text | |
| name_en | text default `''` | |
| kind | text | CHECK `IN ('industry','concept')` |
| status | text default `active` | CHECK `IN ('active','proposed','retired')` |
| source | text | CHECK `IN ('industry_chain','concept_research','llm_discovery')` |
| created_at, updated_at | timestamptz | |

### `theme_memberships` (one (theme, symbol) row)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| theme_id | uuid FK→themes | CASCADE |
| symbol | text | |
| market | text | CHECK `IN ('TW','US')` |
| sub_industry | text default `''` | |
| confidence | float default `1.0` | CHECK `>= 0 AND <= 1` |
| source | text | CHECK `IN ('industry_chain','concept_research','llm_discovery')` |
| created_at | timestamptz | |
| unique | `(theme_id, symbol)` | serves the screener's "symbols in theme X" hot path as an index range scan |
| index | `(symbol, market)` | serves the reverse "themes for symbol X" lookup |

- No soft-delete column: a refresh **replaces per `(theme_id, source)` in one transaction** (delete
  that source's rows, insert the fresh snapshot), so every row present is current and the read path
  stays predicate-free. Mirrors the ingest store's replace-by-partition philosophy.

### `theme_tiers` (one (theme, sub_industry) upstream/midstream/downstream position — *populated*)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| theme_id | uuid FK→themes | CASCADE |
| sub_industry | text | |
| tier | text | CHECK `IN ('upstream','midstream','downstream')` |
| source | text | CHECK `IN ('curated','heuristic')` |
| updated_at | timestamptz | |
| unique | `(theme_id, sub_industry)` | keyed on for the idempotent upsert; curated rows are never overwritten by a heuristic pass |

- Populated on the monthly cadence right after `theme_memberships`: a curated editorial map
  (`config/theme_tiers.yaml`) writes `source='curated'` rows for the major themes, then the
  keyword heuristic backfills every other membership sub-industry with `source='heuristic'`.
  Curated-wins is enforced at the write (a heuristic write against an already-curated row is a
  no-op) as well as by the backfill only visiting pairs the curated pass did not cover.

### `supply_chain_edges` (one directed symbol→symbol relationship — *populated*)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| from_symbol, to_symbol | text | |
| market | text | CHECK `IN ('TW','US')` |
| edge_type | text | CHECK `IN ('supplies','customer','competes')` |
| confidence | text | CHECK `IN ('high','medium','low')` — provenance transparency band, **not** a numeric score |
| revenue_pct | float null | |
| source | text | CHECK `IN ('seed','llm_extraction')` |
| from_name, to_name | text default `''` | |
| created_at, updated_at | timestamptz | |
| index | `(from_symbol)`, `(to_symbol)` | both directions of "edges touching symbol X" must be fast |

- Populated in two ways, each a **replace-per-`source` snapshot** (a re-run replaces that source's
  rows rather than duplicating): a hand-seeded bootstrap YAML (`config/supply_chain_seed.yaml`,
  `source='seed'`) of well-known TW/global relationships, and an in-theme-only LLM extractor
  (`source='llm_extraction'`) that may connect only a single theme's own members — a proposed edge
  to a symbol outside the theme is rejected in code.

### `theme_change_log` (audit trail of theme/tier/edge writes — *populated*)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| entity | text | CHECK `IN ('theme','membership','tier','edge')` |
| entity_ref | text | human-identifiable key — theme key, or `"supply_chain_seed"` etc. |
| action | text | CHECK `IN ('create','update','replace')` |
| source | text | the producing source (`concept_research`/`seed`/`llm_extraction`/…) |
| detail | text | short human summary (≤200 chars) — member count, confidence distribution; **not** a payload dump |
| run_id | text | correlates every row a single seed/refresh invocation produced |
| created_at | timestamptz | |
| index | `(run_id)` | "what did one run change" |

- Append-only. Deferred out of the migration that shipped the other theme tables and added in its
  own migration by the layer that first writes it.

## 3d. Multi-factor score cache ([modules/20](modules/20-analytics-scoring.md) §2c)

### `stock_scores` (one symbol's cached daily composite score, per horizon profile — *populated*)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| symbol, market | text | `market` CHECK `IN ('TW','US')` |
| as_of | text (ISO date) | the trading day the batch scored, not a timestamp |
| profile | text | CHECK `IN ('swing','position')` |
| composite | float null | 0-100, or `null` if every factor was unavailable that day |
| band | text | CHECK `IN ('偏多','中性','偏空')` |
| `{momentum,trend_quality,value_guard,quality,revenue_flow}_percentile` | float null (x5) | one column triplet per factor, not one JSON blob — a fixed five-factor shape, so flat columns are simpler and index-free reads faster than unpacking JSON |
| `{...}_description` | text (x5) | the human-readable basis for each factor's percentile |
| `{...}_unavailable_reason` | text null (x5) | why a factor is `null`, when it is |
| partial_factors | JSON array | factor names excluded from the composite blend that day |
| regime_caveat, non_trading_caveat, normalization_caveat | text | always non-empty |
| computed_at | timestamptz | |
| unique | `(symbol, market, profile, as_of)` | the write-side upsert key; a re-run for the same day replaces the row rather than accumulating history |
| index | `(market, profile, as_of, composite)` | serves the leaderboard's `WHERE market=? AND profile=? AND as_of=? ORDER BY composite` sort-and-slice directly off the index |

- No `user_id` — platform-wide reference data, like the theme tables above, not per-user state.
  Unlike the theme tables, this one carries a date (`as_of`): a score is genuinely a new value
  every trading day, so it is a same-day snapshot cache rather than slow-moving classification
  data — replaced (upserted) once a day by `analytics/scoring_batch.py::job_scoring_batch`, never
  accumulated as a history. Read through `analytics/scoring_store.py`'s `get_cached_score`
  (point lookup) and `get_leaderboard` (sort-and-slice), never as raw rows above that seam.

---

## 4. Feedback & quality ([modules/13](modules/13-chat-interaction-features.md))

### `turn_feedback` (built — a minimal thumbs + comment capture)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| turn_id | uuid FK→turns | CASCADE |
| user_id | uuid | denormalized, not FK-constrained (matches the rest of this schema's per-row `user_id` convention) |
| rating | text | CHECK `IN ('up','down')` |
| comment | text null | CHECK length ≤ 500 |
| created_at, updated_at | timestamptz | |
| unique | `(turn_id, user_id)` | **upsert target** — a re-submit updates the one row (rating/comment/`updated_at`) rather than accumulating duplicates |
| index | `(turn_id)`, `(user_id)` | |

**Built:** `POST /threads/{id}/turns/{tid}/feedback` writes a real row here via
`SqlAlchemyFeedbackStore.submit`. **Not built:** the rolling 7-day per-skill quality score, the
`<60%`-with-`≥10`-samples alert threshold, and the admin `feedback_events`/`feedback_alerts`
tables + `GET /feedback/quality`, `GET /feedback/alerts`, `GET /feedback/recent`,
`PUT /feedback/alerts/{id}/acknowledge|resolve` endpoints this section once described — that
quality-score/alert pipeline remains a dedicated follow-up, not a table in this schema today.

---

## 5. Usage & quotas ([modules/10](modules/10-events-hooks.md), [modules/12](modules/12-multi-user-tiers.md))

### `usage_events` (real, built — the durable per-turn usage rollup)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| user_id | uuid, indexed | |
| turn_id | text | |
| input_tokens, output_tokens | int default 0 | |
| tool_calls | int default 0 | |
| finmind_calls | int default 0 | |
| cost_micros | int default 0 | LLM cost, **int micro-units** (SQLite `REAL` loses precision) |
| created_at | timestamptz | |
| unique | `(user_id, turn_id)` | one rollup row per completed turn |
| index | `(user_id, created_at)` | rollups/future `GET /usage` |
- The write-through target of a finished turn's `UsageAccumulator.snapshot()` — append-only,
  distinct from `usage_counters` below (the live enforcement gate). No `kind`/`provider`/`model`/
  `audio_seconds`/`token_breakdown` columns exist today — voice-cost and prompt-composition
  attribution are not broken out per-row yet, unlike an earlier draft of this schema described.
  High-volume → time-range partition candidate.

### `usage_counters` (real, built — the authoritative daily-chat-limit enforcement gate)
| Column | Type | Notes |
|--------|------|-------|
| user_id | text | part of PK |
| counter | text | part of PK — e.g. `chat` |
| window_key | text | part of PK — e.g. a UTC-day string like `2026-08-05` |
| n | int default 0 | CHECK `>= 0` |
| updated_at | timestamptz | |
| pk | `(user_id, counter, window_key)` | |
| index | `(user_id)` | |
- A generic `(user_id, counter, window_key) → n` counter, not one fixed row-shape per metric —
  simpler than an earlier draft of this schema that sketched separate `chats`/`requests`/
  `finmind_calls`/`cost` columns. **Concurrency:** mutated only via an atomic
  `INSERT … ON CONFLICT DO UPDATE SET n = n + :delta` upsert, never read-then-write, so two
  concurrent turns for the same user/window can't both slip past the cap — this lives in the app
  DB (not Redis) on purpose, since it must survive a Redis flush. The FinMind Sponsor-pool
  ceiling is a **separate** mechanism entirely (`usage/finmind_pool.py`'s Redis-backed
  reserve-before-act pool, §Governance §9), not one of this table's counters.

---

## 6. Scheduler & alerts ([modules/14](modules/14-scheduler-alerts.md))

### `schedule_jobs`
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| user_id | uuid FK→users | |
| trigger_type | text | `cron` \| `market_event` |
| trigger_spec | text | cron expr or `after_close`/`pre_open` |
| trigger_market | text default `TW` | CHECK `IN ('TW','US')` |
| skill_ref | text | governed skill name |
| skill_inputs | json | |
| read_only | bool default true | enforced true (carve-out §9a: no streaming/poll) |
| delivery_channels | json array | portable (SQLite dev); matches `alert_rules.channels`/`alert_preferences.channels` — never PG `text[]` (§Portability) |
| quiet_start, quiet_end | time null | |
| daily_limit | int null | |
| enabled | bool | |
| last_run, next_run | timestamptz | |
| status | text | `ok`\|`running`\|`skipped_quota`\|`error` (`running` detects orphaned runs after a crash) |
| version | int | optimistic lock (worker vs user edit) |
| created_at | timestamptz | |
| index | partial `(next_run) WHERE enabled` — due-jobs worker scan | |

### `alert_rules`
| id | user_id FK | symbol | market | condition json | channels (json array) | quiet_start/end | daily_limit | enabled | last_triggered | created_at |
- `symbol` + `market` (CHECK `IN ('TW','US')`) match the multi-market `Symbol` model
  ([modules/19](modules/19-data-access-router.md)) / `watchlist_items` — a US alert is disambiguated
  (was a bare TW-only `stock_id`). Same stale-vs.-`Market`-enum gap as `watchlists.market` (§3a
  note) — `GB`/`JP`/`EU` are real markets now but not in this CHECK.
- index partial `(market, symbol) WHERE enabled` + `(user_id)` — alert-engine scan + list mine.

### `alert_preferences`
| user_id PK/FK | channels (json array) | enabled_categories (json array) | quiet_start/end | daily_limit | morning_digest bool |

### `device_push_tokens` (native APNs/FCM push — [modules/14](modules/14-scheduler-alerts.md) §4)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| user_id | uuid FK→users | CASCADE; a user may register several devices |
| platform | text | CHECK `IN ('ios','android')` |
| token | text | provider push token |
| created_at, last_seen | timestamptz | `last_seen` refreshed on app open; prune tokens APNs/FCM reports invalid |
| unique | `(user_id, token)` | idempotent register (`POST /me/devices`); `DELETE /me/devices/{token}` on logout/rotation |
| index | `(user_id)` | fan-out a notification to a user's devices |

### `notifications` (delivered)
| id | user_id FK | source (`schedule`\|`alert`\|`digest`) | source_id | channel | dedupe_key | payload json | delivered_at | read_at |
- **`UNIQUE (user_id, dedupe_key)`** where `dedupe_key = (source_id, trigger_date, channel)` →
  at-least-once execution with idempotent delivery, user-scoped for isolation consistency (a
  redelivered LINE/Telegram/native push is a no-op; see [modules/16](modules/16-infrastructure.md) §5).
- index `(user_id, delivered_at DESC)`; partial `WHERE read_at IS NULL` (unread badge).

---

## 7. Skills (registry metadata — definitions live in `backend/src/skills/`)

### `skill_registry` — **design only, not built**

**Not built.** No `skill_registry` table exists in `backend/src/db/models.py`. Skills are
discovered entirely from `<dir>/*/SKILL.md` frontmatter at process startup
(`skills/registry.py`'s `SkillRegistry.load`) — there is no DB-backed cache/version table today,
and nothing in this codebase writes one. The shape below is the design, kept for if a queryable
DB projection of the file-based registry is ever needed.
| name PK | version | description | tools json | enabled | updated_at |

---

## 8. Pets & gamification ([modules/21](modules/21-pet-gamification.md))

Cosmetic-only — **no tier/entitlement/data linkage**. App state, `user_id`-scoped.

### `user_pets` (owned pets — one row per obtained pet)
| Column | Type | Notes |
|--------|------|-------|
| id | uuid PK | |
| user_id | uuid FK→users | cascade |
| pet_id | text | catalog key (definitions in code, not a table) |
| is_active | bool default false | equipped flag; ≤1 true per user (enforced in the equip tx) |
| level | int default 1 | max = `pets.max_level` config |
| xp | int default 0 | `pets.xp_curve` config, rolls over surplus |
| obtained_via | text | `pull` \| `admin` (CHECK) |
| created_at, updated_at | timestamptz | |
| index | `(user_id)`, `(user_id, is_active)` | |

### `user_pet_stats` (one row per user)
| Column | Type | Notes |
|--------|------|-------|
| user_id | uuid PK/FK→users | |
| total_pulls | int default 0 | |
| streak_days | int default 0 | consecutive-day logins |
| last_login_date | date null | streak computation |
| chat_count | int default 0 | drives per-N-chats bonus pulls |
| available_pulls | int default 1 | **new users seeded with 1** (free register pull) |
| pity_counter | int default 0 | guaranteed rare/legendary thresholds; resets on hit |
- **Concurrency:** pull consumes `available_pulls` via **reserve-before-act** (atomic decrement);
  XP/streak/chat increments are atomic upserts — parallel turns/devices can't double-grant or
  lose an update ([modules/16](modules/16-infrastructure.md) §10). No market data, no PII beyond
  the login-streak date.

---

## Search (FTS) — title + message content
Two artifacts (the store protocol has one impl per engine — no shared SQL):
- **Title search:** trigram/`tsvector` index on `threads.title`.
- **Message-content search** (`GET /threads/search`, [modules/13](modules/13-chat-interaction-features.md)):
  a dedicated **`message_search`** FTS artifact keyed `(user_id, thread_id, turn_id)` indexing
  **only extracted plain text from `messages.content` WHERE type='text'** — never `thinking`/
  `tool_use`/raw payloads (governance + noise).
- **CJK (Traditional Chinese):** default tokenizers don't segment CJK. Use **`pg_trgm` GIN**
  (or `zhparser`/`pg_jieba`) on Postgres and the **FTS5 `trigram` tokenizer** on SQLite, so
  "台積電外資" substring queries work. This drives the index choice above.

## Concurrency & integrity
- **Per-user isolation (defense in depth):** primary = a mandatory session-scoped store wrapper
  that injects `WHERE user_id = ?` (single enforcement seam the isolation tests target); prod
  backstop = **Postgres Row-Level Security** on PII/conversation/billing tables keyed on a
  request GUC. **The GUC MUST be `SET LOCAL app.user_id` inside a per-request transaction** — a
  session-level `SET` leaks across users under transaction-mode connection pooling (PgBouncer/
  serverless poolers multiplex one connection across users), which would be a cross-user data leak.
  Every isolation-sensitive query therefore runs inside a transaction that sets the GUC locally
  (or pin session-mode pooling for RLS connections). SQLite (dev) relies on the wrapper only.
- **Enums as CHECK constraints:** all constrained-`text` columns (`tier`, `status`, `role`,
  `type`, `window`, `trigger_type`, `data_class`, …) use `text + CHECK (col IN (...))` — portable
  to both engines, not Postgres native `ENUM`.
- **Optimistic locking:** `version int` on concurrently-mutated rows (`threads`,
  `subscriptions`, `schedule_jobs`) — compare-and-set to avoid lost updates.
- **In-app sequences:** `stream_events.event_seq` / `messages.seq` are assigned in-process
  (not DB sequences), guarded by `UNIQUE(turn_id, seq)`.
- **Secrets**: only `user_secrets.ciphertext` holds credentials (KMS envelope-encrypted,
  see [modules/16](modules/16-infrastructure.md) §8); never in `messages`, `journal_entries`,
  `usage_events`, or logs.
- **No market data persisted here**: it lives in the DuckDB/Parquet + Redis tier
  ([modules/16](modules/16-infrastructure.md) §3); this DB is product/app state only. TW data-quirk
  handling (price 0-on-no-trade, back-adjusted prices, 興櫃 open-outside-range) lives with the market
  store — [modules/22](modules/22-data-ingest.md) §4 validators + [modules/20](modules/20-analytics-scoring.md) §1.

## SQLite ↔ Postgres portability
- **UUID PKs** = `text` (app-generated) on both — don't rely on `gen_random_uuid()`.
- **Arrays** (`tags`, channels, categories): **Postgres-only** → store as **JSON array** for
  portability (or normalize a `thread_tags` join table if tag-filter queries need indexing).
- **Money** (`cost`, `amount`): **integer micro-units** — SQLite has no exact decimal (`REAL`).
- **JSON vs JSONB:** plain `json` (blocks are write-once, extracted for FTS out-of-band);
  `jsonb` only on Postgres where we query *into* content.
- **`timestamptz`:** UTC ISO-8601; date math in app or engine-specific fn.
- **FTS engines** differ entirely (above) — behind the store protocol.
- **`ON DELETE CASCADE`:** works on both, but SQLite needs `PRAGMA foreign_keys=ON` per
  connection — set in the connection factory or cascades silently no-op.
- Partial indexes + `ON CONFLICT` upsert are portable (SQLite ≥ 3.24 / 3.8).

## Migrations & retention
- **Migrations:** Alembic; every migration must apply on **both SQLite and Postgres in CI**
  (expand-contract for message-tree / `stream_events` changes). See [modules/16](modules/16-infrastructure.md) §9.
- **Retention (concrete):** `stream_events` — **TTL purge** (delete where `created_at < now() − N`,
  kept > turn completion); **not** time-partitioned (would break `UNIQUE(turn_id, event_seq)`, see the
  table note). Soft-deleted `threads` purged on a scheduled job **with PII scrub** — profile PII
  (`birthday/phone/nationality`) is **overwritten/NULLed** on purge (these are plaintext columns; not
  "crypto-shredded" — only `user_secrets.ciphertext` is encrypted, so crypto-shredding applies there).
  `turn_feedback` has no time-bounded query today (there is no rolling-window quality score built
  yet — see §4) — add it to the TTL purge job if unbounded growth matters once that pipeline lands.
  `billing_events`/`journal_entries`/`usage_events` grow unbounded → **time-range partition (monthly)
  on Postgres** for cheap detach/drop (their PKs include/allow `created_at` in the partition key).
- **Account deletion (GDPR-style):** a "delete my account + all data" flow purges/anonymizes
  across all `user_id`-scoped tables, not just per-resource soft-delete.
