# Governance

These rules are inherited verbatim from the reference project
[`tw-market-live-data-intelligence`](https://github.com/SmartyJohnway/tw-market-live-data-intelligence/)
and are the **single source of truth** that `backend/src/agent/guardrails.py` is written and
tested against. They are non-negotiable and must not be relaxed by any module, tool, or
interface.

## 1. Prime directives

1. **Context-only, non-trading.** The agent produces analytical context with explicit
   caveats. It **never** emits buy/sell/hold signals, price targets, position sizing, or
   any actionable trading recommendation.
2. **No real-time guarantee.** No source is real-time. Every output carries a currentness
   caveat. Live-ish sources are bounded snapshots, never streams.
3. **No autonomy over the network.** No auto-polling, no startup network calls, no
   full-market scans. *(Scheduling/background jobs: forbidden by default — narrowly and
   explicitly amended for user-authorized jobs in §9a.)*
4. **Bounded, user-driven observation.** Live-ish observation (M5K, M8C) is user-driven, one
   bounded check per request — not streaming, not auto-polled. It runs without a separate
   approval step; where it draws on FinMind Sponsor data it is subject to the §4 entitlement
   gate.
5. **Immutable canonical.** M5F canonical packages are never mutated; writes are
   versioned/append-only.
6. **No credentials stored.** No API keys or secrets persisted by the framework; provider
   keys are supplied via environment at runtime only.

## 2. Mode semantics (fixed)

| Mode | Package | Meaning |
|------|---------|---------|
| **Mode A** | M5F | Canonical Package — preserved, immutable market context |
| **Mode B** | M5K | Bounded Observation — explicit, manual, operator-controlled live-ish check |
| **Mode C** | M5N | Conversation Package — AI-safe caveated artifact for discussion |

Mode meanings are fixed. Do not create parallel contracts or redefine a mode.

## 3. Levels

- **Level 1** = M5F canonical (preserved history).
- **Level 2** = M5K bounded observation (explicit live-ish check).

## 4. Data class & the entitlement gate (the enforcement mapping)

All tools are **read-only** and always allowed to run — there is **no safety-tier system and
no confirmation/approval gate**. The only access control is a **data-class entitlement**:
whether a tool/dataset draws on **paid (licensed) market data**.

Every tool and dataset declares a `data_class`. (The value is named `sponsor` after FinMind's
current "Sponsor" plan — the sole paid source today; if a second paid provider is added, read
`sponsor` as the vendor-neutral "licensed/paid" class and map each vendor's plan to it at the
adapter edge. It is a static property of the *data's provenance*, not a per-user billing state.)

| `data_class` | Meaning | Sources | Access |
|--------------|---------|---------|--------|
| **`public`** | Free everywhere | gov OpenAPI (TWSE/TPEX/TAIFEX), FinMind Free/Backer, derived compute | All users, incl. anonymous & MCP |
| **`sponsor`** | Paid FinMind Sponsor data | 分點, 八大官股, 產業鏈資金流, tick/minute-K, real-time snapshots, active-ETF holdings | **Premium/Pro only** |

- A `sponsor` result for an unentitled user is returned as a **masked teaser**
  (`{locked: true, required_tier, preview}`) — the frontend renders it **blurred/馬賽克 with
  an "upgrade to Premium/Pro" CTA badge**, never a hard error. See
  [modules/12](modules/12-multi-user-tiers.md).
- **Top-N-preview exception (row-level, not tool-level):** a `sponsor` tool whose output is a
  ranked row list (e.g. `get_etfs_holding_stock`) may declare a small `free_row_count` of its
  own top rows as `public`-visible in the SAME response, with every row beyond that count
  having its value fields nulled server-side (never the real number sent and merely hidden by
  CSS) — the row's label/rank/shape stays real so the list still reads as a legitimate preview.
  This is the `12-multi-user-tiers.md` §"top-N rows + a locked strip for a Sponsor table"
  pattern; it is an explicit, per-tool, row-scoped exception to the otherwise all-or-nothing
  masked-teaser gate above — a tool opts into it deliberately in its own output model, it is
  never the default, and the *whole-tool* masked-teaser envelope still applies to any `sponsor`
  tool that hasn't opted in.
- `public` data (including live-ish/real-time from *free* sources and gov `*_MIS`) is freely
  callable by everyone and over MCP. Live observation no longer requires any approval.
- `sponsor` tools are still exposed over MCP, but return the masked teaser unless the MCP
  principal is entitled.

## 5. TLS policy (M6D)

TLS verification is **strict by default**. The policy is chosen explicitly with **no
silent fallback**:

| Policy | Behavior |
|--------|----------|
| `strict` (default) | Full certificate verification |
| `compatibility` | Relaxed, but explicitly selected and logged |
| `unsafe-explicit` | Verification disabled, only via explicit opt-in, loudly surfaced |

No code path may downgrade TLS silently.

## 6. Prohibited actions (hard blocks)

The system must never:

- Mutate M5F or change observation semantics.
- Create parallel contracts or bypass authentication.
- Add/store credentials.
- Introduce startup network calls, or schedule/poll observations *(except the bounded §9a
  scheduler carve-out)*.
- Scan the full market.
- Expose raw payloads unnecessarily.
- Produce trading outputs of any kind.

## 7. Where enforcement lives

- **`guardrails.py`** — runs on both proposed tool calls and final agent output. Blocks
  trading language, blocks full-market scans, verifies caveats are present.
- **`accounts/entitlements.py`** — enforces the `sponsor` data-class gate (masked teaser for
  unentitled users). This is the only access gate.
- **`sources/base.py`** — sole owner of network I/O and TLS policy.
- **`context/canonical.py`** — enforces canonical immutability.
- **Governance tests** (highest-priority suite) — assert each rule above holds; see the
  Verification section of the plan and `tests/`.

## 8. AI usage statement

All AI usage in this framework is **context-only, caveated, and non-trading**. Outputs are
analytical context intended to support human judgment, not to make or recommend trades.

The agent is expected to state a clear descriptive/comparative judgment once the evidence
supports it — this is not a relaxation of §1's non-trading rule; the two operate at different
levels (factual characterization vs. actionable instruction).

**Styling layers are content-immutable.** The agent personality ([modules/23](modules/23-agent-personality.md))
and the cosmetic pet persona ([modules/21](modules/21-pet-gamification.md)) affect only *tone /
verbosity / warmth* — they are appended **below** the governance system prompt (order:
governance → personality → skill → pet) and are all subject to `guardrails.check_output`. No
persona, preset, or tone parameter may introduce a trading recommendation, drop a caveat, or
restate a third-party target as our own. Personas are **curated/file-defined, never free user
text** (prompt-injection defense).

## 9. Amendment: multi-user hosting & user-authorized scheduling

The framework has been extended from local-first single-operator to a **multi-user hosted
product** ([modules/12](modules/12-multi-user-tiers.md), [adr/0008](adr/0008-multi-user-hosted.md)).
This amends — but does not weaken — the rules above:

- **Per-user isolation.** Threads, memory, usage, feedback, schedules are scoped by
  `user_id`; no cross-user access. Credentials (OAuth, BYOK, FinMind token) are encrypted at
  rest and never appear in prompts, journal, memory, or logs.
- **User tiers gate only paid data + throughput, never a trading recommendation.** The
  `sponsor` data-class gate (§4) and rate/quota limits are the only things tiers control;
  non-trading and caveat rules apply equally to Free/Premium/Pro.
- **The FinMind Sponsor pool is a single, static, shared budget — not tier-aware per-user
  quota.** One Redis-backed atomic-reservation pool (default **6000 req/hr**) is shared across
  **all** Premium/Pro users; Free never touches it at all (its skills run against the free
  FinMind token, outside this pool). Reservation is reserve-before-act with refund-on-failure/
  under-use (§5 concurrency); a fan-out that would overrun the pool degrades the overrunning
  lanes to a cached/`unavailable_tier` posture rather than spending budget the pool doesn't
  have. There is no live upstream quota-polling endpoint — the ceiling is a static config value,
  not read from FinMind at runtime.

### 9a. Scheduler carve-out (overrides "no scheduler / no auto-poll")

> **Amended by the alerts v2 design** (bounds 2 and 6 below are the amended text; ADR-0009 keeps the original as history). The amendment takes effect when modules/14 P0 ships — until then the v1 code still enforces the Pro gate.

Rules §1.3 and §6 forbade schedulers, polling, and background jobs. The scheduler/alerts
feature ([modules/14](modules/14-scheduler-alerts.md), [adr/0009](adr/0009-scheduler-governance-amendment.md))
is permitted **only** within these hard bounds:

1. **User-authorized only** — a job exists solely because a signed-in user created it. No
   system-initiated polling; no startup network calls remain (jobs run on their own schedule,
   not at app start).
2. **Read-only & non-streaming** — scheduled runs read canonical/official-EOD/history and
   build conversation/health context. They must **not** open live/real-time streams or poll
   at high frequency (that would violate §1.2/§1.3); a coarse post-close snapshot is fine.
   Evaluation reads the **ingested canonical store** (§9c output) — event-driven consumption of
   ingest, no new polling and no per-user upstream fetch. Sponsor-class values reach a non-entitled
   user only as the §4 `{locked, required_tier, preview}` mask, applied **server-side when the
   notification is built** (never sent and hidden client-side).
3. **Non-trading** — scheduled/alert outputs carry the same caveats; observations, never signals.
4. **Quota-bounded** — jobs consume the owner's tier quota; over-quota jobs are skipped and
   reported, never retried in a tight loop.
5. **Coarse, market-aware cadence** — post-close / pre-open / weekday cron aligned to EOD
   availability; **no high-frequency tick polling**.
6. **Open to all signed-in users under uniform fair-use caps** (not a tier feature) and
   auditable — every run **and fire** is journaled with its owner.
7. **Outbound delivery is user-authorized egress** — email / Web Push / LINE / Telegram sends
   are confined to `scheduler/delivery/*`, carry only the user's own notification, never read
   upstream market data, use the §5 TLS policy, and take channel credentials from env only (§1.6).
   Every outbound body passes `guardrails.check_output` first and fails closed to a fixed template.

### 9b. Live-quote carve-out (overrides "no auto-polling / no streaming" for intraday quotes)

Rule §1.3 forbade auto-polling and streaming. The **live-quote feed**
([modules/16](modules/16-infrastructure.md) §2a) is permitted **only** within these hard bounds
— a *demand-driven, bounded observation*, not unattended market scanning:

1. **Demand-driven only** — the server polls a symbol **only while ≥1 signed-in client is
   actively subscribed** to it (an open `/quote/stream`). No connected watcher → no poll. This
   is not a whole-market scan and not a standing background job; the demand set = current
   subscriptions.
2. **Market-hours only, bounded cadence** — one shared poll per demanded symbol at **5s** while
   its market is open (`MarketProfile` hours); auto-stops when the last subscriber disconnects;
   nothing polls outside trading hours.
3. **Fan-out, not per-client polling** — one poll serves all clients watching that symbol (via
   Redis pub/sub), which is *lower* quota use than client-side polling, not higher.
4. **Delayed snapshot, non-trading, caveated** — the feed carries the same ~5–20s currentness
   caveat (§1.2); it is a live-ish display, **not** true real-time and **not** a trading signal.
5. **Feature-gated, not blanket data-class-gated** (Premium/Pro) — the **server-pushed 5s stream**
   (`/quote/stream`) is a **feature-tier** gate (`live_quote_stream`, [12](modules/12-multi-user-tiers.md) §3
   axis 2); Free gets EOD. `data_class` is per **serving adapter** ([19](modules/19-data-access-router.md) §4):
   a `finmind`-served intraday tick is `sponsor`, a `twse_mis`-served tick is `public` — which is why the
   same MIS snapshot is freely available via a **manual** `run_bounded_observation` (public, operator-
   triggered) but the **auto-pushed** stream is Premium (the streaming *feature*, not the datum, is paid).
6. **Bounded resources** — per-connection symbol cap, global demanded-symbol cap, and the
   FinMind Sponsor pool budget (§9) still apply; over-budget degrades to a slower cadence /
   cached, never a tight loop.

### 9c. Platform data-ingest carve-out (overrides "no auto-polling / no full-market scans" for building our own store)

Rules §1.3 and §6 forbade auto-polling and full-market scans. The **platform data-ingest / ETL
layer** ([modules/22](modules/22-data-ingest.md), [adr/0025](adr/0025-data-ingest-etl.md)) —
which populates our own derived DuckDB/Parquet store because the gov OpenAPIs keep no history —
is permitted **only** within these hard bounds. It is distinct from §9a (user-authorized jobs)
and §9b (demand-driven live quotes): ingest is unattended and market-wide but **cache-building,
not per-user observation**, so it must be written down here rather than assumed under §9a.

1. **Purpose-bounded** — solely to build/maintain the platform's own historical store from
   date-range-queryable sources (FinMind whole-market-by-date) and to snapshot latest-only gov
   OpenAPIs that otherwise have no history. Not for any other network activity.
2. **Cadence-bounded** — fixed post-publication crons (one pass per dataset per trading day),
   staggered to each dataset's publish time; scrapers (MOPS/PTT/ETF-PCF/rebalance) at **coarse**
   cadence only, with backoff + circuit breaker ([16](modules/16-infrastructure.md) §6/§7). **No
   high-frequency / tick polling.** A **dev-only startup catch-up** (`INGEST_ON_STARTUP`,
   [22](modules/22-data-ingest.md) §6a) is permitted as a *manual* trigger of the same gap-fill;
   **production boots clean** (no per-worker startup fetch) — ingest is owned by the leader scheduler.
3. **Calendar-bounded** — every job gated on the trading-day list (`TaiwanStockTradingDate`) +
   the NCDR typhoon-closure overlay; nothing runs on non-trading days.
4. **Not user data, not trading** — writes the **shared derived store**, never per-user state;
   outputs stay caveated (currentness preserved through the cache tier); nothing here issues a
   trade or a signal.
5. **Quota-fair** — ingest draws from a **separate quota bucket** from the §9b live poller and
   §9a user jobs (fair-share, [16](modules/16-infrastructure.md) §4), so a backfill can't starve
   live users. Cache-first: ingest writes each canonical row once; user reads hit the cache.
6. **Auditable & leader-elected** — every run is journaled (dataset, date, rows, status); a
   single leader ([16](modules/16-infrastructure.md) §5) prevents double-writes. FinMind token
   stays env-only, decrypt-at-call, never crossing the scheduler boundary in plaintext (§6).

Everything not explicitly permitted here (or in §9a / §9b) remains prohibited.
