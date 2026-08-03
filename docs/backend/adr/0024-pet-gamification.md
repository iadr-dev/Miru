# ADR 0024 — Pet & gamification: cosmetic companion, decoupled from entitlements

## Status
Accepted. Adds [modules/21](../modules/21-pet-gamification.md); `pets`/`user_pet_stats` tables
in [database-schema.md](../database-schema.md) §8; `/pets/*` in [api-reference.md](../api-reference.md);
`pet.*` SSE events in [modules/07](../modules/07-conversation-streaming.md) / [10](../modules/10-events-hooks.md).

## Context
The user wants a gamified companion: pull a pet on register, level it up, and have it appear in
the agent chat — like a game. Kestrel already ships this (`models/pet.py`: catalog, gacha, pity,
XP/levels, agent-chat persona). The design risk is coupling: if a pet could unlock data, quota,
or a "better answer," a cosmetic becomes a pay-to-win investment tool — a governance and fairness
problem. Three product decisions were confirmed with the user: **free guaranteed pull on
register**, **cosmetic + companion only**, **reactive companion in chat**.

## Decision
Add a `pets/` module that is **fully decoupled from billing, tiers, entitlements, and the
analysis path**:
- **Catalog + gacha** — fixed catalog across 4 rarities; weighted pull with a **pity system**
  (guaranteed rare/legendary at thresholds); duplicates convert to XP; first pet auto-equips.
- **XP/leveling** — earned by chatting (XP to the equipped pet), login streaks, and duplicate
  pulls; milestones grant bonus pulls. Max level + curve are config.
- **Onboarding** — new user gets exactly **one free pull** (welcome gacha modal); returning
  users accrue streak/XP/pulls.
- **Reactive companion** — the equipped pet injects a small **persona hint** that flavors the
  assistant's *tone* only, appended **after** the governance prompt and **subordinate to
  guardrails** (`check_output` still runs). It emits distinct **`pet.reaction` / `pet.leveled` /
  `pet.pull_earned` SSE events** rendered in a separate companion UI region.
- **State** — `user_pets` + `user_pet_stats` in the app DB, `user_id`-scoped; all mutations are
  atomic upserts / reserve-before-act (concurrency §10). **No tier check anywhere in the module.**

## Consequences
- Pets add engagement with **zero governance surface**: no data access, no quota, no analytical
  power, not billed, not gated. A pet can never change a trading-relevant output.
- The persona is tone-only and guardrail-subordinate, so the non-trading + caveat rules hold
  regardless of which pet is equipped; mascot chatter is UI-separated from analysis.
- Adds two small tables + `/pets/*` endpoints + three additive SSE event types; the client can
  ignore all of it with no effect on answers.
- Gacha/pity/XP mechanics are generic; only names/descriptions + the persona prompts carry TW
  flavor, so the system is reusable if the market set expands.
