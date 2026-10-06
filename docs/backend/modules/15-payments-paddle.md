# Module: Payments — Paddle (`backend/src/payments/`)

Subscription billing via **Paddle** ([API reference](https://developer.paddle.com/api-reference/)).
Paddle is the merchant of record (handles tax/VAT/invoicing). This module turns a Paddle
subscription into a user **tier + BYOK entitlement** consumed by `accounts/entitlements`
([12-multi-user-tiers.md](12-multi-user-tiers.md)). See [../adr/0011](../adr/0011-paddle-payments.md).

## 1. Plans & price IDs

Two paid tiers (**Premium**, **Pro**) × **monthly/annual** × **BYOK / non-BYOK** = 8 prices.
**BYOK = user supplies their own LLM key** (they pay inference cost) → **lower price**. On the
pricing page a **"Use my own API key (BYOK)" checkbox** switches the selected `price_id`.

| Tier | Billing | Variant | Price ID (env) |
|------|---------|---------|----------------|
| Premium | monthly | standard | `PADDLE_PRICE_PREMIUM_MONTHLY` = `pri_01kwecmbfvxz36zjsj92v3s3dd` |
| Premium | annual | standard | `PADDLE_PRICE_PREMIUM_ANNUAL` = `pri_01kwecparg397q27hm6849s73h` |
| Premium | monthly | BYOK | `PADDLE_PRICE_PREMIUM_BYOK_MONTHLY` = `pri_01k742qcapxszbnfb4wj3yq4vz` |
| Premium | annual | BYOK | `PADDLE_PRICE_PREMIUM_BYOK_ANNUAL` = `pri_01k742rvx257yqt4qer4tt7z11` |
| Pro | monthly | standard | `PADDLE_PRICE_PRO_MONTHLY` = `pri_01kwecraxw1dtjy76bps3e4w7e` |
| Pro | annual | standard | `PADDLE_PRICE_PRO_ANNUAL` = `pri_01kwecsnxen3b0dkpzrp7gfwf5` |
| Pro | monthly | BYOK | `PADDLE_PRICE_PRO_BYOK_MONTHLY` = `pri_01kwecdxp69zfsy823bv7bkdqy` |
| Pro | annual | BYOK | `PADDLE_PRICE_PRO_BYOK_ANNUAL` = `pri_01kwecgd74836j1qxmxpg2d04d` |

The mapping `price_id ↔ (tier, interval, byok)` lives in `payments/plans.py` (config), the
single place that knows these IDs. Amounts/currency come from Paddle, not hardcoded.

## 2. Checkout flow

1. Pricing page: user picks tier + interval, optionally ticks **BYOK** → frontend resolves
   the `price_id` from `GET /billing/plans`.
2. `POST /billing/checkout {price_id}` → server creates a **Paddle transaction/checkout**
   (Paddle API) with `custom_data={user_id}` and returns the checkout URL / client token
   (Paddle.js overlay).
3. User pays on Paddle. Paddle is the merchant of record.
4. Entitlement is granted on the **webhook**, not on redirect (redirect is UX only).

## 3. Webhooks → entitlement (source of truth)

`POST /billing/webhook/paddle` verifies the **Paddle-Signature** (HMAC over the **raw body**,
not parsed JSON) and processes:

| Event | Action |
|-------|--------|
| `subscription.created` / `subscription.activated` | set `tier` + `byok` from price_id; store subscription |
| `subscription.updated` | re-map tier/interval/byok (plan change, BYOK toggle, proration = Paddle's job). If it carries `scheduled_change: {action: cancel, effective_at}`, **display "cancels on <date>" but do NOT downgrade yet** (status stays `active`) |
| `subscription.canceled` | **the actual downgrade** → `tier=free`, re-mask sponsor. Fires *at* `effective_at` for a scheduled cancel, or immediately when Paddle's dunning exhausts a `past_due` sub. |
| `subscription.past_due` | grace state; entitlements unchanged during Paddle's dunning retry window |

- **Idempotent** (`billing_events.paddle_event_id` unique) **and order-safe**: store the
  event's `occurred_at`; apply the mapping only when `occurred_at >= subscriptions.updated_from_event_at`
  so a late-delivered older event can't regress state (e.g. resurrect a canceled sub).
- Webhook is the **only** writer of `tier`/`byok`; the app never trusts client claims.
- **`users.tier` is a derived cache** written only by the webhook from the active
  subscription's price_id; `subscriptions` is the record; entitlement reads the cache (free =
  no active row). One source of truth, no drift.
- On downgrade to `free`, sponsor data reverts to masked teaser automatically (governance §4).
- Card-update, invoices/PDFs, dunning, proration, tax, and **refunds/disputes are handled by
  Paddle** (MoR); the app reacts to refunds only via `subscription.canceled`. We build none of it.

## 4. BYOK coupling

- A **BYOK subscription requires a stored BYOK key** ([12-multi-user-tiers.md](12-multi-user-tiers.md) §5).
  If a BYOK subscriber removes all keys, the app warns and falls back to platform inference
  only within the standard-price fair-use limit (or prompts to switch plan) — it never
  silently bills the higher standard rate.
- `entitlements.byok(user)` = derived from the active subscription's price_id, exposed in `/me`.

## 5. Endpoints (`api/routers/billing.py`)

```
GET   /billing/plans?country=XX      tier×interval×byok → price_id + price{amount,currency_code} (Paddle,
                                     minor units, pre-tax, country override applied; null if unavailable)
POST  /billing/checkout {price_id}   create Paddle checkout (custom_data.user_id)
GET   /billing/subscription          current subscription — **reconcile-on-read**: read through to
                                     the Paddle API and upsert the local row (self-heals a missed webhook)
POST  /billing/portal                Paddle-hosted management URL (update card / cancel / invoices)
POST  /billing/webhook/paddle        Paddle events (signature-verified) — entitlement source of truth
GET   /billing/history               **proxies Paddle's transactions API** (or deep-links the portal) —
                                     no local invoices mirror; Paddle owns the tax-compliant invoices/PDFs
```

**Reconciliation (correctness):** the local `subscriptions` row is normally webhook-written,
but `GET /billing/subscription` reconciles-on-read against Paddle so a dropped/delayed webhook
self-heals on the next page load (no separate ledger job needed at this scale).

## 6. Config / secrets

```
PADDLE_API_KEY            = server-side key            # encrypted at rest, never client-exposed
PADDLE_WEBHOOK_SECRET     = signature verification
PADDLE_CLIENT_TOKEN       = Paddle.js (publishable)
PADDLE_ENVIRONMENT        = sandbox | production        # config.settings.paddle_environment
PADDLE_PRICE_*            = the 8 IDs above
```

## Governance obligations
- Payment credentials/keys are encrypted, never logged/journaled/prompted ([governance.md](../governance.md) §6/§9).
- Tier/BYOK changes are webhook-driven and journaled; entitlement is never client-asserted.
- Billing changes access/throughput only — never trading behavior; all governance rules
  apply equally across paid tiers.
