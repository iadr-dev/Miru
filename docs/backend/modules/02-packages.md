# Module: Context pipeline (`backend/src/context/`)

Assembles source results into the governed context packages. Preserves the reference
project's **mode/level semantics** (kept as governance concepts) while using clean domain
filenames. Concept ↔ code mapping is in [../architecture.md](../architecture.md) §1.

## `MarketDataStore` protocol — the persistence seam for market data

Like `ConversationStore`/`MemoryStore`, market/derived data sits behind a **protocol**, not an
ad-hoc store: `MarketDataStore` (read/write canonical packages, observation runs, the
normalized `etf_holdings` table, cached source results). The concrete impl (DuckDB/Parquet +
Redis read-through) lives in [16-infrastructure.md](16-infrastructure.md) §3 and is selected by
config — the "protocol + config-driven impl" idiom, applied to the hot data path too.

**Layering (preserves ADR-0002 "only `sources/` does I/O"):** `context/` requests a
**capability for a (symbol, market)** from the **data-access router**
([19-data-access-router.md](19-data-access-router.md)) — it never names a source or opens a
socket/DuckDB handle. The router resolves per-market priority + fallback and returns a
normalized result; the read-through cache is a caching adapter wrapper in `sources/`. Order:
`context/` → router → `MarketDataStore` (cache hit) → on miss, caching adapter → real source →
write back. This is what makes `context/` market-agnostic (works for TW today, US/JP later).

## Canonical (Mode A / Level 1) — `canonical.py`

- Build and **validate** the immutable canonical market-context package.
- **Immutable:** an existing canonical package is never mutated; writes are versioned/append-only
  via `MarketDataStore` (see [../database-schema.md](../database-schema.md) + [16](16-infrastructure.md) §3).

## Bounded Observation (Mode B / Level 2) — `observation.py`

- One **explicit, operator-triggered** live-ish observation. No polling/scheduling.
- Backed by the `run_bounded_observation` tool; free-source snapshots are `public`, paid
  (FinMind Sponsor) real-time is `sponsor` (entitlement-masked).
- Writes an observation-run record (auditable).

## Conversation (Mode C) — `conversation.py`

- Assembles the **AI-safe artifact**: caveated, no raw payloads, non-trading framing.
- **This is the object the agent reasons over** and the artifact handed to any external chat.

## Source Health — `source_health.py`

- Diagnostics on provider currentness and closure evidence (via the `ncdr_closure` adapter)
  and FinMind tier/quota status.
- Produces the health view surfaced at `GET /health` and over MCP (`get_source_health`).

## Multi-source builder (M8A/B/C) — `builder.py`

| Sub | Scope | Status |
|-----|-------|--------|
| M8A | Official EOD merge (TWSE/TPEX/TAIFEX OpenAPI + FinMind history) | primary |
| M8B | TAIFEX derivatives context | primary |
| M8C | `TAIFEX_MIS` live-ish + real-time snapshots | **staged behind a validation flag; `sponsor` where it uses paid FinMind data** |

## Governance obligations

- Never mutate the canonical package.
- The conversation package must strip raw payloads and inject caveats before leaving this layer.
- M8C remains behind a validation flag until verified; its paid-data portions are `sponsor`
  (entitlement-masked), not approval-gated.
