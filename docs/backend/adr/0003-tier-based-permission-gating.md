# ADR 0003 — Data-class entitlement gating (supersedes safety-tier gating)

## Status
Accepted. **Supersedes** the original tier-based permission-gating decision (superseded by
[0010](0010-remove-safety-tiers.md)).

## Context
The original design gave every tool a numeric `safety_tier` (0/1/2) and gated Tier-2
"bounded observation" behind an out-of-band confirmation token + a HITL approval handshake.
Product direction changed: all TW market data here is read-only and non-destructive, so an
approval prompt adds friction without protecting anything. The one thing that genuinely needs
gating is **paid data** — FinMind **Sponsor** datasets — which is a *commercial* entitlement,
not a *safety* concern.

## Decision
Remove the `safety_tier` system and the confirmation/approval gate entirely. Replace with a
two-value **`data_class`** on every tool/dataset:
- `public` — gov OpenAPI + FinMind Free/Backer + derived compute → free for all (incl. MCP).
- `sponsor` — requires the FinMind Sponsor plan → **Premium/Pro** users only.

A `sponsor` tool called by an unentitled user returns a **masked teaser**
(`{locked, required_tier, preview}`); the frontend renders it blurred (馬賽克) with an upgrade
CTA. Enforced in the `pre_tool` hook via `accounts/entitlements`. See governance §4.

## Consequences
- No approval handshake, no `awaiting_approval` state, no `/approvals` endpoint, no
  confirmation token — all removed.
- All tools (incl. live-ish/real-time from free sources) run freely; live observation no
  longer needs approval. Real-time via *paid* FinMind is simply `sponsor`-gated.
- MCP exposes all read-only tools; `sponsor` ones return the teaser unless entitled.
- Gating logic collapses to one entitlement check, reused by the paywall/upgrade UX.
- Non-streaming / no-auto-poll rules (governance §1) still hold independently.
