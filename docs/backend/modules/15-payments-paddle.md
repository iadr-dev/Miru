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
2. `POST /billing/checkout {tier, interval, byok}` → server creates a **Paddle transaction**
   (Paddle API) with `custom_data={user_id}` and returns its `transaction_id`. A user who already
   has a live (non-canceled) subscription gets `409 already_subscribed` — they change or cancel in
   the Paddle portal (`POST /billing/portal`), never a second checkout.
3. The browser opens **Paddle.js' checkout overlay** on the settings page
   (`Paddle.Checkout.open({transactionId})`), initialised with the publishable
   `PADDLE_CLIENT_TOKEN` + `environment` returned by `GET /billing/plans`. Paddle Billing has no
   hosted checkout page; the `checkout.url` it returns points at the account's *default payment
   link* and is not used. Paddle is the merchant of record and collects tax at this step.
4. Entitlement is granted on the **webhook**, not on the overlay's `checkout.completed` event (which
   only tells the page to start polling `GET /billing/subscription` until the row appears).
   The Paddle notification destination must be `<public base>/api/v1/billing/webhook/paddle`.

## 3. Webhooks → entitlement (source of truth)

`POST /billing/webhook/paddle` verifies the **Paddle-Signature** (HMAC over the **raw body**,
not parsed JSON) and processes:

| Event | Action |
|-------|--------|
| `subscription.created` / `subscription.activated` | set `tier` + `byok` from price_id; store subscription |
| `subscription.updated` | re-map tier/interval/byok (plan change, BYOK toggle, proration = Paddle's job). If it carries `scheduled_change: {action: cancel, effective_at}`, **display "cancels on <date>" but do NOT downgrade yet** (status stays `active`) |
| `subscription.canceled` | **the actual downgrade** → `tier=free`, re-mask sponsor. Fires *at* `effective_at` for a scheduled cancel, or immediately when Paddle's dunning exhausts a `past_due` sub. |
| `subscription.past_due` | grace state; entitlements unchanged during Paddle's dunning retry window |
| `subscription.paused` | billing is stopped, so nothing is entitled: `tier=free`, status `paused`, the row (and its plan) is kept for the resume. A `subscription.updated` that reports `status: paused` is treated the same |
| `subscription.resumed` | restores the plan tier from the price (`status: active`) |

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
POST  /billing/portal?purpose=       Paddle customer-portal link (portal session): overview | payment
POST  /billing/change-plan           switch tier/interval/BYOK on the live subscription (Paddle prorates)
POST  /billing/resume                undo a scheduled cancel
GET   /billing/invoice/{txn_id}      invoice PDF link, only for the caller's own transactions
POST  /billing/webhook/paddle        Paddle events (signature-verified) — entitlement source of truth
GET   /billing/history               **proxies Paddle's transactions API** (or deep-links the portal) —
                                     no local invoices mirror; Paddle owns the tax-compliant invoices/PDFs
```

**Reconciliation (correctness):** the local `subscriptions` row is normally webhook-written,
but `GET /billing/subscription` reconciles-on-read against Paddle so a dropped/delayed webhook
self-heals on the next page load (no separate ledger job needed at this scale).

### Subscription lifecycle (verified against Paddle sandbox)

| Action | How | Result in Miru |
|--------|-----|----------------|
| Subscribe | Paddle.js overlay -> webhook `subscription.created/activated` | row `active`, tier set, page polls until it appears |
| Change plan | `POST /billing/change-plan` (Paddle's portal only offers card update + cancel) | Paddle prorates and charges; local row/tier refreshed immediately, webhook is then a no-op |
| Cancel | Paddle portal "Cancel subscription" -> ends at period end | webhook `subscription.updated` with `scheduled_change`: status stays `active`, `scheduled_cancel_at` set, page shows "ends <date>" + Resume |
| Resume | `POST /billing/resume` | `scheduled_change` cleared, banner gone |
| Period ends / immediate cancel | webhook `subscription.canceled` | row `canceled`, tier `free`, page shows the plan picker again |
| Card declined at checkout | Paddle's overlay shows the bank's decline | no subscription is created |
| Payment fails on renewal | webhook `transaction.payment_failed` -> `subscription.past_due` *(verified in sandbox with the renewal-decline card)* | status `past_due`, tier unchanged during dunning; page shows "update payment method", lists the failed charge as unpaid (no receipt) and no "next charge" date |
| Recover from past due | customer pays the overdue amount from the card form (`POST /billing/portal?purpose=payment` opens it with the amount due) | `transaction.paid` + `subscription.activated`: status `active` again |
| Pause | Paddle (dashboard / API `POST /subscriptions/{id}/pause`); the customer portal does not offer it | webhook `subscription.paused`: status `paused`, tier `free`, page shows "paused - no paid features, not billed" + Resume |
| Resume a pause | `POST /billing/resume` -> Paddle `POST /subscriptions/{id}/resume` (immediately) | webhook `subscription.resumed`: plan tier restored |

`subscriptions.tier` holds the tier the row currently *entitles* (so `free` while paused or ended); the plan being
paid for is always derivable from `price_id`, which is what the page displays.

Transactions that never charged anything (`draft`, `ready`, `canceled`) are not shown in billing history.

**Testing a renewal failure in sandbox:** subscribe with the "approve the first charge, decline renewals" test
card (`4000 0027 6000 3184`), then move the billing date with `PATCH /subscriptions/{id}`
`{next_billed_at, proration_billing_mode: do_not_bill}`. Paddle requires the new date to be at least
**30 minutes** away (`subscription_next_billed_at_too_soon`), so the renewal - and its decline - happens about 30
minutes later. The webhook destination must be reachable at that moment.
A subscriber cannot start a second checkout (`409 already_subscribed`).

## 6. Config / secrets

```
PADDLE_API_KEY            = server-side key            # encrypted at rest, never client-exposed
PADDLE_WEBHOOK_SECRET     = signature verification
PADDLE_CLIENT_TOKEN       = Paddle.js (publishable)
PADDLE_ENVIRONMENT        = sandbox | production        # config.settings.paddle_environment
PADDLE_PRICE_*            = the 8 IDs above
```

## Go-live checklist (sandbox -> production)

Everything above was exercised against Paddle **sandbox**. Before taking real money:

1. **Paddle live account** approved (business verification, bank details, tax settings).
2. **Live catalogue**: recreate the product and the 8 prices (Premium/Pro x monthly/annual x BYOK) in
   the live account, including the Taiwan `unit_price_overrides` (TWD) if prices differ by country.
3. **Secrets** in the production environment (never committed): `PADDLE_API_KEY` (live),
   `PADDLE_CLIENT_TOKEN` (`live_...`, publishable), `PADDLE_WEBHOOK_SECRET` (the *live* destination's
   secret), the 8 `PADDLE_PRICE_*` ids, and `PADDLE_ENVIRONMENT=production`.
4. **Webhook destination** in the live account: `<public API base>/api/v1/billing/webhook/paddle`
   (HTTPS, a stable domain - not a tunnel), subscribed to the `subscription.*` and `transaction.*`
   events. Send a test event and confirm a 200.
5. **Checkout domain**: the live Paddle.js only opens on an *approved domain* - add the production web
   origin under Paddle's checkout settings, and set the account's default payment link to a real page.
6. **Customer portal** enabled in the live account (card update, cancel, invoices).
7. **Legal & copy**: terms, privacy and refund policy pages linked where you sell; Paddle is the
   merchant of record and shows its own terms in checkout. Check all plan copy stays free of
   trading language (governance).
8. **Smoke test with a real card** on the cheapest plan: subscribe, see the tier change, download the
   receipt, change plan, cancel, confirm the tier returns to free at period end, then refund it.
9. **Monitoring**: alert on webhook non-2xx responses and on `billing_provider_error`.

## Governance obligations
- Payment credentials/keys are encrypted, never logged/journaled/prompted ([governance.md](../governance.md) §6/§9).
- Tier/BYOK changes are webhook-driven and journaled; entitlement is never client-asserted.
- Billing changes access/throughput only — never trading behavior; all governance rules
  apply equally across paid tiers.
