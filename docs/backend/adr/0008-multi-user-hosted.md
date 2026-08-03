# ADR 0008 — Multi-user hosted product (from local-first single-operator)

## Status
Accepted. Amends the local-first assumption of ADR 0006/0007 and governance §9.

## Context
The design began local-first, single-operator (no auth, no tiers). The product direction is
now a **hosted multi-user service** with tiers (free/premium/pro), BYOK, per-user history,
feedback, and monetized features — mirroring the Kestrel reference. This touches every
stateful module (threads, memory, usage, feedback, schedules).

## Decision
- Add `accounts/` with OAuth (Google/LINE), a `users` table, and
  `entitlements.py` as the single source of truth for tier→limits and feature→min-tier.
- **Scope all stateful data by `user_id`** at the store layer (conversation, memory, usage,
  feedback, schedules). Anonymous users get read-only market data only.
- **Tiers gate AI features and Sponsor datasets only**; market data/watchlist/news stay free.
  Unentitled access returns a **teaser envelope** (`{locked, required_tier, preview}`), not 401.
- **FinMind rate limits are tier-aware**: Free 600/hr, Sponsor 6000/hr; shared Sponsor token
  pooled by the quota manager and surfaced via M5Q.
- **BYOK** lifts the daily chat limit; keys encrypted at rest, never logged/journaled/prompted.

## Consequences
- Enables monetization and multi-tenant use; requires auth, secret storage, and per-user
  isolation tests (no cross-user leak) as first-class concerns.
- Governance is preserved and extended (§9), not weakened: non-trading/caveat rules apply to
  all tiers equally; the `sponsor` data gate (see [0010](0010-remove-safety-tiers.md)) is the
  only access control.
- Operational cost tracking per user becomes essential (usage accounting from ADR 0006).
- If ever reverted to local-first, a single "owner" user satisfies the same model.
