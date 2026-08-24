# Module: Context pipeline (`backend/src/context/`)

Assembles source results into the governed context packages. Preserves the reference
project's **mode/level semantics** (kept as governance concepts) while using clean domain
filenames. Concept ↔ code mapping is in [../architecture.md](../architecture.md) §1.

## `MarketDataStore` protocol — design intent, not yet built

[ADR-0018](../adr/0018-enforcement-seam-and-store-protocols.md) §3 decided that market/derived
data should sit behind a **protocol**, not an ad-hoc store — `MarketDataStore` (read/write
canonical packages, observation runs, the normalized `etf_holdings` table, cached source
results), peer to `ConversationStore`/`MemoryStore`, with a read-through cache as a caching
`SourceAdapter` wrapper in `sources/` so `context/` itself never opens a socket/DuckDB/Redis
handle.

> **Partially built:** the `MarketDataStore` protocol itself is still a confirmed **future
> design intent**, not current code — there is no `MarketDataStore` protocol anywhere in
> `context/` today. But the read-through caching wrapper ADR-0018 anticipated **is** built and
> wired: `sources/caching_adapter.py`'s `CachingSourceAdapter` implements the `SourceAdapter`
> protocol as a genuine Redis read-through cache — per-capability TTL keyed off each result's own
> `TimingClass`, a single-flight-on-miss lock (`SET NX EX`) with poll-and-wait to guard against
> stampedes on a hot key. `dataaccess/composition.py::build_default_adapters` wraps every
> adapter with it when `settings.market_data_cache_backend == "redis"`, it has a dedicated test
> module (`backend/tests/sources/test_caching_adapter.py`), and it is enabled by default in
> `docker-compose.yml`. What's still missing is the rest of the `MarketDataStore` surface —
> canonical-package/observation-run persistence and the normalized `etf_holdings` table — plus
> the leader-writes-Parquet / workers-read-only split (`ingest/store.py`'s `ParquetStore` +
> per-worker read-only DuckDB, [16-infrastructure.md](16-infrastructure.md) §3), which remains
> the durable-storage half of that seam. Building the full protocol is a follow-up, not a
> current gap in the request path: `context/` still performs no I/O of its own today (the router
> does the fetching, now through a cache-wrapped adapter), so ADR-0002's "only `sources/` does
> I/O" invariant holds regardless of whether the full protocol lands.

**Layering (preserves ADR-0002 "only `sources/` does I/O"):** `context/` requests a
**capability for a (symbol, market)** from the **data-access router**
([19-data-access-router.md](19-data-access-router.md)) — it never names a source or opens a
socket/DuckDB handle. The router resolves per-market priority + fallback and returns a
normalized result from the configured adapter(s), each of which is Redis-cache-wrapped by
`CachingSourceAdapter` when `market_data_cache_backend == "redis"` (the pending piece is only
the rest of the `MarketDataStore` seam above — canonical/observation persistence — not the cache
tier itself). This is what makes `context/` market-agnostic (works for TW today, US/JP later)
regardless of that remaining seam.

## Canonical (Mode A / Level 1) — `canonical.py`

- Build and **validate** the immutable canonical market-context package.
- **Immutable in memory today:** `CanonicalPackage` is a frozen Pydantic model;
  `new_version()` returns a new object with `version + 1` rather than mutating in place
  (enforced by the framework, not just convention). This module performs **no I/O** — there is
  no durable canonical-package store yet (that would be the pending `MarketDataStore` seam
  above); a caller that wants a canonical package persisted across requests has nowhere to
  write it to today.

## Bounded Observation (Mode B / Level 2) — `observation.py`

- One **explicit, operator-triggered** live-ish observation. No polling/scheduling.
- Backed by the `run_bounded_observation` tool; free-source snapshots are `public`, paid
  (FinMind Sponsor) real-time is `sponsor` (entitlement-masked).
- Writes an observation-run record (auditable).

## Conversation (Mode C) — `conversation.py`

- Assembles the **AI-safe artifact**: caveated, no raw payloads, non-trading framing.
- **This is the object the agent reasons over** and the artifact handed to any external chat.

## Source Health — `source_health.py`

- A capability-agnostic **reducer**, not a diagnostics source of its own: it aggregates the
  `SourceHealth`/`HealthStatus` already carried on each already-fetched `SourceResult` (from a
  `CanonicalPackage`, an `ObservationRun`, or a raw `{capability: SourceResult}` map) into one
  `SourceHealthReport` per `(symbol, market)` build. No I/O, and no special-casing for any
  specific adapter — it does not itself call `ncdr_closure` or read FinMind tier/quota state;
  whatever health/caveat a capability's serving adapter already stamped on its `SourceResult` is
  what gets surfaced here.
- Produces the health view surfaced at `GET /health` and over MCP (`get_source_health`).

## Multi-source builder (M8A/B/C) — `builder.py`

| Sub | Scope | Status |
|-----|-------|--------|
| M8A | Official EOD merge (TWSE/TPEX/TAIFEX OpenAPI + FinMind history) | primary — in the default capability set |
| M8B | TAIFEX derivatives context (`derivatives_oi`, via the real `taifex_openapi` adapter) | built and configured, but **opt-in**: excluded from the default capability set, requested only via `BuildOptions.include_derivatives=True` |
| M8C | `TAIFEX_MIS` live-ish + real-time snapshots | **staged behind a validation flag; `sponsor` where it uses paid FinMind data** |

## Governance obligations

- Never mutate the canonical package.
- The conversation package must strip raw payloads and inject caveats before leaving this layer.
- M8C remains behind a validation flag until verified; its paid-data portions are `sponsor`
  (entitlement-masked), not approval-gated.
