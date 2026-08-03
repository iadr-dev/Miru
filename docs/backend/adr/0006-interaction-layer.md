# ADR 0006 — Rich chat interaction layer (streaming, threads, skills, memory, events)

## Status
Accepted; the HITL/Tier-2 approval parts are **superseded by [0010](0010-remove-safety-tiers.md)**
(no approval gate — paid `sponsor` data is entitlement-masked instead).

## Context
The initial design exposed the agent as a single `POST /agent/query` with vague "streams
steps" SSE and in-memory session state. That is not enough for a Claude Code-style chat
backend: there was no typed event protocol, no conversation persistence/resume, no mid-turn
approval mechanic for Tier-2, no reusable analysis playbooks, no cross-session memory, and
no observability/accounting seam.

## Decision
Add an interaction layer around the agent runtime, all under existing governance:

1. **Typed SSE event protocol** + resumable streams (`Last-Event-ID`), projected from an
   internal **event bus** — one source of truth for both clients and internal subscribers.
2. **Persistent threads** (SQLite, pluggable) with list/get/fork/resume and per-turn
   lifecycle (running/cancelled/done/error) + cancellation + idempotency.
3. ~~HITL approval handshake~~ — **removed by [0010](0010-remove-safety-tiers.md)**. Paid
   (`sponsor`) data instead returns a masked teaser via `tool.locked`; no turn parking.
4. **Skills**: versioned playbooks with tool allowlists, guardrail-bound.
5. **Memory**: working/session/long-term; long-term recalled as background context and
   re-verified before use; no credentials/payloads/trading content stored.
6. **Hooks + accounting**: vetoing lifecycle hooks (guardrails are mandatory), usage/cost
   accounting, and rate limiting (provider spend + FinMind 600/hr).

## Consequences
- The system becomes a real chat backend: resumable, auditable, extensible.
- More moving parts (event bus, store, hooks) — mitigated by keeping the bus in-process and
  the store behind a protocol for a local-first default.
- Guardrails are re-expressed as mandatory hooks, unifying enforcement at `pre_tool` /
  `pre_output` / `pre_turn`.
- MCP stays read-only (all tools; `sponsor` masked when unentitled); thread mutations are
  API-only, preserving the safe external-handoff model.
