# ADR 0010 — Remove safety tiers & approval gate; gate only paid (Sponsor) data

## Status
Accepted. Supersedes [0003](0003-tier-based-permission-gating.md); amends [0006](0006-interaction-layer.md)
(HITL), [0007](0007-dynamic-orchestration-and-triggering.md), [0009](0009-scheduler-governance-amendment.md).

## Context
The framework carried two overlapping "tier" concepts: (1) **safety Tier 0/1/2** with a
confirmation-token / HITL approval gate on live observation, and (2) **user tiers**
(free/premium/pro) + **FinMind plan tiers**. The safety-tier gate added friction with no real
protection (all data is read-only), and the only thing that actually needs gating is **paid
FinMind Sponsor data**.

## Decision
1. **Delete the safety-tier system** (`SafetyTier` enum, `safety_tier`/`max_tier` fields) and
   the **entire approval flow**: `tool.approval_required`, `awaiting_approval` turn status,
   the `/approvals` endpoint, confirmation tokens, and the HITL handshake.
2. **Introduce `data_class` = `public | sponsor`** on tools/datasets. `sponsor` = requires the
   FinMind Sponsor plan.
3. **Gate `sponsor` data by user tier** (Premium/Pro). Unentitled → **masked teaser**
   (`{locked, required_tier, preview}`) → frontend blur (馬賽克) + upgrade CTA. New SSE event
   `tool.locked` replaces `tool.approval_required`.
4. All tools become freely runnable and are all exposed over MCP (sponsor ones masked).

Retained unchanged: non-trading, caveat enforcement, no-full-market-scan, and the
no-streaming / no-auto-poll rules (governance §1) — these never depended on tiers.

## Consequences
- Simpler runtime: one entitlement check, no token plumbing, no parked turns.
- The paywall UX (blur + CTA) is now a first-class, uniform behavior across chat, cards,
  scheduler output, and MCP.
- "User tier" and "FinMind plan tier" remain and are the only tiers in the system.
- Docs updated repo-wide; ADR 0003 rewritten, this ADR records the removal.
