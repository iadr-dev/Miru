# ADR 0009 — Scheduler & alerts (governance amendment)

## Status
Accepted. Amends governance §1.3 and §6 (no scheduler / no auto-poll / no background jobs).
Amended by [0010](0010-remove-safety-tiers.md) — scheduled jobs are read-only/non-streaming
(not "Tier 0/1"); `sponsor`-data jobs are entitlement-masked, no approval gate.

## Context
The inherited governance forbids schedulers, polling, and background jobs — appropriate for a
local, on-demand context workbench. The product now requires "ask the agent to do scheduled
jobs" (post-close briefs, price/institutional alerts, morning digest), which cannot be done
on-demand only. Silently ignoring the rule would be dishonest; removing it wholesale would
lose the safety it provided.

## Decision
Permit scheduling/alerts **only** within hard bounds, codified in governance §9a:
1. **User-authorized** — a job exists only because a signed-in user created it; no
   system-initiated polling; no startup network calls.
2. **Read-only & non-streaming** — read canonical/official-EOD/history + build
   conversation/health context. No live/real-time streams or high-frequency polling.
   Sponsor-data jobs require the owner's entitlement (masked otherwise).
3. **Non-trading** — same caveats; observations, not signals.
4. **Quota-bounded** — consumes the owner's tier quota; over-quota → skip + report, no loop.
5. **Coarse, market-aware cadence** — post-close/pre-open/weekday cron; no tick polling.
6. **Entitlement-gated (Pro)** and fully journaled.

Implementation: durable job store + single worker; a scheduled run is just an agent turn with
no live human, routed through the same guardrails.

## Consequences
- Delivers proactive value while keeping the strongest safety property (no unattended live
  observation, no trading output).
- Introduces background infrastructure (worker, job store, delivery channels) — new failure
  and abuse surfaces, mitigated by quota + entitlement gating and per-run journaling.
- The "no scheduler" purity of the reference is intentionally traded for product capability,
  documented here and in governance §9a so the change is explicit and auditable.
