# Module: Context pipeline (`backend/src/context/`)

Assembles source results into the governed context packages. Preserves the reference
project's **mode/level semantics** (kept as governance concepts) while using clean domain
filenames. Concept ↔ code mapping is in [../architecture.md](../architecture.md) §1.

## `MarketDataStore` protocol — still pending, not built

Like `ConversationStore`/`MemoryStore`, market/derived data was designed to sit behind a
**protocol**, not an ad-hoc store: `MarketDataStore` (read/write canonical packages, observation
runs, the normalized `etf_holdings` table, cached source results), with a DuckDB/Parquet + Redis
read-through concrete impl selected by config. **This protocol does not exist in the real
codebase** — zero references anywhere under `backend/src/`. The real market-data read path
today is `context/builder.py` → the data-access router → adapters directly for live/on-demand
reads, or, for already-ingested derived data, `ingest/reader.py`'s `ParquetMarketDataReader`
(read-only DuckDB over the leader's Hive-partitioned Parquet — see
[16-infrastructure.md](16-infrastructure.md) §3 and [22-data-ingest.md](22-data-ingest.md)).
There is no unifying protocol seam between these two paths yet; this remains a real future-design
item, not a load-bearing part of the request path today.

**Layering (preserves ADR-0002 "only `sources/` does I/O"):** `context/` requests a
**capability for a (symbol, market)** from the **data-access router**
([19-data-access-router.md](19-data-access-router.md)) — it never names a source or opens a
socket/DuckDB handle. The router resolves per-market priority + fallback and returns a
normalized result. This is what makes `context/` market-agnostic (works for TW today, US/JP later).

## Canonical (Mode A / Level 1) — `canonical.py`

- Build and **validate** the immutable canonical market-context package.
- **Immutable:** `canonical.py` performs no I/O itself and holds no durable store of its own
  today (see the `MarketDataStore` note above) — an existing package is never mutated in
  memory; a durable, versioned/append-only write path is part of the same still-pending seam.

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
| M8B | TAIFEX derivatives context | real, configured adapter (`taifex_openapi`) — but **opt-in**: a default build never requests derivatives; the caller must pass `BuildOptions.include_derivatives=True` |
| M8C | `TAIFEX_MIS` live-ish + real-time snapshots | **staged behind a validation flag; `sponsor` where it uses paid FinMind data** |

## Governance obligations

- Never mutate the canonical package.
- The conversation package must strip raw payloads and inject caveats before leaving this layer.
- M8C remains behind a validation flag until verified; its paid-data portions are `sponsor`
  (entitlement-masked), not approval-gated.
