# ADR 0018 — Enforcement-seam dependency inversion, MarketDataStore protocol, domain vocabulary

## Status
Accepted. Refines [modules/05](../modules/05-agent-runtime.md),
[modules/10](../modules/10-events-hooks.md), [modules/02](../modules/02-packages.md),
[modules/16](../modules/16-infrastructure.md), [governance.md](../governance.md) §7. From the
software-architecture review.

## Context
The review found three structural defects in an otherwise-coherent design:
- **C1** — the runtime loop (`agent/`) called hooks defined in `events/` (interaction) that
  delegate to `accounts/entitlements` (product), an **upward dependency** against the one-way
  layer rule; and three docs disagreed on who owns the `sponsor` check.
- **C2** — "Turn" had two definitions (loop's inner LLM round vs the schema's request/response
  cycle), and "Trace" existed in prose but mapped to no table.
- **C3** — the market-data cache tier had **no protocol** (unlike every other store) and its
  read-through put I/O inside `context/`, violating ADR-0002's "only `sources/` does I/O".

## Decision
1. **Enforcement seam is dependency-inverted:** the `Hook` protocol + dispatch points live in
   the **runtime kernel**; interaction/product layers *register* handlers (entitlement data
   arrives via `ToolContext`). The runtime never imports handler modules. **Ownership split:**
   `agent/guardrails.py` = **governance only** (non-trading, no-scan, caveats); a **separate
   mandatory `entitlement` hook** owned by `accounts/entitlements` does `sponsor` masking.
   Modules 05/10 and governance §7 now say the same thing.
2. **Domain vocabulary fixed:** **Turn** = request/response cycle (schema + module 07 are
   authoritative); the loop's inner unit is a **Step**; **"Trace" is removed** (hierarchy is
   `thread → turns → messages`).
3. **`MarketDataStore` protocol** introduced (peer of `ConversationStore`/`MemoryStore`);
   DuckDB/Parquet+Redis is its config-selected impl. Read-through is a **caching `SourceAdapter`
   in `sources/`**, so `context/` performs no I/O and ADR-0002 holds.

## Consequences
- The one-way dependency rule now has no leaks; guardrails (core invariant) and entitlement
  (product concern) are cleanly separated but both mandatory.
- Naming is consistent across loop, schema, journal, and SSE (`turn.*` events bracket the whole
  assistant Turn; Steps are internal).
- The hottest data path gains the same protocol seam as every other store.
- Also recorded minor design-quality items applied alongside: `DeliveryChannel`/`VoiceProvider`
  protocols (extension parity), the three-axis "capability resolution" note (data_class /
  tier-feature / per-turn consent), and `data_class`'s vendor-neutral reading.
