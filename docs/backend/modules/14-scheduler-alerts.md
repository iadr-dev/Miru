# Module: Scheduler & alerts (`backend/src/scheduler/`)

User-authorized scheduled jobs and price/event alerts — e.g. "每天收盤後給我 2330 的籌碼摘要"
or "外資連 3 天買超就通知我". This **deliberately amends** the inherited no-scheduler/no-poll
governance, under strict bounds. See the amendment in [../governance.md](../governance.md) §9a
and [../adr/0009](../adr/0009-scheduler-governance-amendment.md).

## 1. Why this needs a governance amendment

The reference project forbids schedulers, auto-polling, and startup network calls. A hosted
multi-user product with "ask the agent to do scheduled jobs" fundamentally requires
background execution. Rather than silently break the rule, the amendment permits scheduling
**only** within these bounds:

- **User-authorized:** a job exists only because a specific signed-in user created it; no
  system-initiated polling.
- **Read-only & non-streaming:** scheduled runs read canonical/official-EOD/history and build
  conversation/health context. They may **NOT** open live/real-time streams or high-frequency
  polling (that would violate the no-streaming/no-poll rules). Sponsor-data jobs require the
  owner to hold the entitlement.
- **Non-trading:** outputs are the same caveated, non-trading context as interactive chat.
- **Quota-bounded:** jobs consume the owner's tier quota (chats/day, FinMind class); a job
  that would exceed quota is skipped and reported, never silently retried in a loop.
- **Market-aware, not tick-polling:** schedules are coarse (post-close, morning digest,
  weekday cron), aligned to EOD availability — not high-frequency polling.
- **Scheduler feature is entitlement-gated** (Pro; see [12](12-multi-user-tiers.md)).

## 2. Scheduled jobs

```
ScheduleJob
 ├─ id, user_id, created_at
 ├─ trigger: cron | market_event (e.g. after_close, pre_open)
 ├─ action: skill_ref + inputs        # reuses a governed skill (e.g. daily-context-brief)
 ├─ delivery: [channel...]            # in-app, line, telegram, email
 ├─ quiet_hours, daily_limit
 └─ enabled, last_run, next_run, status
```

- Backed by a durable job store + a single worker (APScheduler/Celery-beat class), **not**
  an in-request loop. Runs are executed through the **same agent runtime + guardrails** as
  interactive turns — a scheduled run is just a turn with no live human in the loop.
- A scheduled run touching `sponsor` data requires the owner's entitlement; if absent, that
  section is omitted/masked in the delivered brief (no upgrade prompt spamming).

## 3. Alerts (rules engine)

```
AlertRule
 ├─ id, user_id, symbol, market   (multi-market; matches watchlist_items / module 19 Symbol)
 ├─ condition: price/volume/indicator/institutional threshold  (evaluated on EOD/official data)
 ├─ channels, quiet_hours, daily_limit
 └─ enabled, last_triggered
AlertPreference: channels, enabled_categories, quiet_start/end, daily_limit, morning_digest
```

- Evaluated by the same worker on official/EOD data cadence. Alert copy is caveated and
  non-trading ("2330 外資買超連 3 日" — an observation, not "buy").

## 4. Delivery channels

- Behind a **`DeliveryChannel` protocol** (`send(notification, recipient) → receipt`), one impl
  per channel — in-app (persisted), LINE, Telegram, email, **native push (APNs/FCM)** — selected by
  config. Adding a channel = one new impl, no scheduler change (same "protocol + config-driven impl"
  idiom as `SourceAdapter`/`LLMClient`). Channel credentials are the platform's or the user's, stored
  encrypted (never in prompts/journal).
- **Native push (mobile).** Devices register a push token; a notification's payload carries a
  **deep-link** so a tap opens the right surface. `device_push_tokens(user_id, platform ios|android,
  token, created_at, last_seen)` — a user may have several devices; a token that APNs/FCM reports
  invalid is pruned. Payload: `{title, body, deep_link:{route, symbol?, thread_id?}, notification_id}`
  — the client resolves `deep_link` to the target screen (Stock Detail / a thread / an alert's symbol),
  with a cold-land skeleton if the app was killed ([mobile interaction-spec §7.3](../../mobile/interaction-spec.md)).
  LINE binding already exists (§4 above); native push is the additional mobile channel.
- Respects per-user quiet hours and daily notification limits.

## 5. Endpoints

```
POST   /schedules              create (validated: user-authorized, read-only, quota-checked)
GET    /schedules              list mine
PATCH  /schedules/{id}         enable/disable/edit
DELETE /schedules/{id}
POST   /alerts                 create alert rule
GET    /alerts                 list mine
PATCH/DELETE /alerts/{id}
GET    /notifications          my delivered notifications
GET    /notifications/badge     lightweight unread/pending counts {unread_notifications, unread_threads, pending_alerts} — for the mobile tab-island + top-bar bell dots
PUT    /me/alert-preferences   channels, quiet hours, digest
POST   /me/devices             register a native push token {platform, token} (idempotent on token)
DELETE /me/devices/{token}     unregister (logout / token rotation)
```

## Governance obligations
- Enforces the §9a amendment: user-authorized, read-only, non-trading, quota-bounded, coarse
  cadence, no unattended live observation.
- Every scheduled/alert run is journaled with its owner and is auditable like any turn.
- Jobs are disabled automatically if the owner's entitlement lapses or quota is exhausted.
