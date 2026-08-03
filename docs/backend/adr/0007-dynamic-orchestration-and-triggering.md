# ADR 0007 — Dynamic orchestration + web-native skill triggering

## Status
Accepted. Supersedes the slash-command-primary assumption in the initial ADR 0006 skill design.
The sub-agent gating aspects are amended by [0010](0010-remove-safety-tiers.md) — sub-agents
are read-only and `sponsor` data is entitlement-masked; there is no approval/tier gate.

## Context
Two open questions:
1. Are slash commands needed? They are a CLI affordance; this product targets web/mobile chat.
2. Should the agent use a single agent or parallel sub-agents? Left undecided in the plan.

The Kestrel agent architecture (`Kestrel/docs/kestrel/agent-architecture.md`) is a proven
reference for a TW-stock chat agent. It resolves both: no slash commands (natural-language
intent classifier instead), and a **dynamic** classifier that picks the execution framework
(none/single/subagent/team) and 0–4 skills per query rather than hardcoding.

## Decision
1. **Triggering.** Skills are triggered primarily by the **intent classifier** from natural
   language; secondarily by **UI action chips** (GUI equivalent of a slash command); `/skill`
   is an optional, non-load-bearing power-user alias. Slash commands are not a primary
   mechanism.
2. **Orchestration.** Add `backend/src/orchestrator/` with an LLM intent classifier returning
   `{framework, skills, reason}`:
   - `none` — reply, no tools
   - `single` — one ReAct agent
   - `subagent` — N skills → N **parallel** agents → synthesis (independent dimensions)
   - `team` — N agents with **shared context**, sequential → synthesis (findings build up)
   Framework + skill count are decided per query, with a keyword-matching fallback if the
   classifier call fails.
3. **Model routing.** The orchestrator names a *role* (classifier/agent/subagent/synthesis);
   the provider-agnostic LLM adapter maps role → model, enabling cost tiering.
4. **Web-native events.** Add `rich_card`, `ask_user`, `follow_up`, `route.decided`,
   `agent.progress` to the SSE protocol so the chat renders cards, inline clarifications, and
   follow-up chips.

## Consequences
- The "single vs parallel" decision is made by the system per query — the caller/UI never
  chooses. Matches a mature reference implementation.
- Governance holds: each sub-agent inherits its skill's tool allowlist; all sub-agents are
  read-only; `sponsor` data is entitlement-masked uniformly (see
  [0010](0010-remove-safety-tiers.md)); routing + provenance are journaled; concurrency is capped.
- `ask_user` (clarification) remains a UX signal, distinct from `tool.locked` (the paid-data
  entitlement signal).
- More LLM calls per complex query (classifier + sub-agents + synthesis) → mitigated by
  cost-tiered model routing and a concurrency cap.
