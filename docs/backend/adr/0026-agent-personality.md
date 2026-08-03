# ADR 0026 — Agent personality: user-selectable curated presets, style-only

## Status
Accepted. Adds [modules/23](../modules/23-agent-personality.md); `users.personality_id` +
`threads.personality_override` in [database-schema.md](../database-schema.md); `personality`
flag on `POST /agent/query` ([api-reference.md](../api-reference.md) §1); injected in
[modules/05](../modules/05-agent-runtime.md) `transform_context`.

## Context
The product needs a configurable **analyst voice** for the AI itself — distinct from the pet
(cosmetic mascot nudge, [0024](0024-pet-gamification.md)) and from skills (task procedures). The
risk in any "personality" feature is that a persona weakens the non-trading/caveat guarantees, or
that a free-text persona box becomes a prompt-injection and governance hole.

## Decision
**User-selectable, curated presets — style-only, subordinate to guardrails.**
- **Preset registry** (`personality/presets/*.yaml`, file-defined + schema-validated like skills):
  a curated prompt **fragment** + **enumerated tone params** (verbosity/jargon/structure/warmth).
  Four starters: `steady` (default) / `coach` / `concise` / `data`. **No free-text persona input.**
- **Fixed prompt layering:** `[1] governance → [2] personality → [3] skill body → [4] pet hint`,
  all subordinate to `guardrails.check_output`. Personality can only *style*, never override
  content; the four presets share one governance floor.
- **Selection:** `users.personality_id` (Settings; default `steady`), optional
  `threads.personality_override`, optional one-shot `POST /agent/query {personality}`. Resolution:
  turn → thread → user → default. Resolved **per turn** (no stale voice; concurrency §16.10).
- **Not tier-gated** by default — voice is a UX preference free to all tiers (paid buys
  data/throughput, not a "smarter voice"). Journaled per turn (id+version) for reproducibility.

## Consequences
- A safe, testable personality surface: presets are curated and governance-reviewed, so tone
  varies while non-trading/caveat/provenance rules are provably intact (guardrail backstop unchanged).
- One new prompt segment (measured in `token_breakdown.system_prompt`), two nullable columns, one
  query flag — no new hot-path cost, no loop/tool budget impact.
- Clear separation of the three "voice-ish" concepts: **personality** (base analyst voice, this
  ADR) vs **skill** (what to do) vs **pet** (cosmetic warmth) — each a distinct prompt layer.
- Free-text personas explicitly rejected (prompt-injection + governance); if a premium preset is
  ever added it is entitlement-gated like any feature, but the starters stay free.
