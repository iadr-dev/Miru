# ADR 0011 — Paddle for subscription billing, with BYOK-priced variants

## Status
Accepted.

## Context
The multi-user product ([0008](0008-multi-user-hosted.md)) needs paid Premium/Pro tiers.
We want a merchant-of-record that handles global tax/VAT/invoicing, and a pricing model where
users who **bring their own LLM key (BYOK)** pay less (they cover inference). Eight price IDs
are already provisioned in Paddle (Premium/Pro × monthly/annual × BYOK/standard).

## Decision
- Use **Paddle** as MoR; add `backend/src/payments/` with `plans.py` mapping the 8 `price_id`s
  to `(tier, interval, byok)`.
- Pricing page has a **BYOK checkbox** that switches the selected `price_id` to the cheaper
  BYOK variant.
- **Webhook is the single source of truth** for `tier`/`byok`: signature-verified,
  idempotent (`billing_events`), writes `subscriptions` and updates `users.tier`. Redirects
  are UX only; the client never asserts entitlement.
- Downgrade/cancel reverts to `free` at period end → sponsor data auto-reverts to masked
  teaser (governance §4).
- BYOK subscription requires a stored BYOK key; removing keys warns/limits rather than
  silently billing the standard rate.

## Consequences
- Tax/compliance offloaded to Paddle; less PCI/VAT burden.
- One dependency for revenue; mitigated by webhook idempotency + reconciliation via
  `GET /billing/subscription` against Paddle.
- Entitlement logic stays in `accounts/entitlements`, fed by `payments/` — clean separation.
- Price IDs live in config only; amounts/currency are read from Paddle, never hardcoded.

## Amendment (billing review — keep it minimal, MoR-appropriate)
- **Cancel model corrected:** a user cancel arrives as `subscription.updated` with
  `scheduled_change` (display only, stay `active`); the real downgrade is `subscription.canceled`
  at `effective_at` (and where Paddle dunning exhausts a `past_due` sub). Don't downgrade early.
- **Order-safety:** persist event `occurred_at`; apply only if newer than `updated_from_event_at`.
- **`users.tier` is a derived cache** of the active subscription's price_id (single source of truth).
- **Dropped as over-engineering:** the local `invoices` mirror + `transaction.completed` handler
  (Paddle owns invoices/PDFs → `/billing/history` proxies Paddle) and the `subscriptions.version`
  optimistic lock (webhook is the only writer; ordering guarded by `occurred_at`). Refunds/
  disputes handled in Paddle; app reacts only via `subscription.canceled`.
