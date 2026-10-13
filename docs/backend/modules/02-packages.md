# Module: Context pipeline (`backend/src/context/`)

Assembles source results into the governed context packages. Keeps the project's
**mode/level semantics** (Mode A/B/C, Level 1/2) while using clean domain filenames. Concept ↔ code mapping is in [../architecture.md](../architecture.md) §1.

## Market-data caching and the `MarketDataStore` seam

Why: market/derived data should sit behind a seam, not an ad-hoc store, so `context/` itself never
opens a socket/DuckDB/Redis handle. There is **no `MarketDataStore` protocol class** in `context/`
today; the seam is realised in two concrete pieces:

- **Read-through cache** — `sources/caching_adapter.py`'s `CachingSourceAdapter` implements the
  `SourceAdapter` protocol as a Redis read-through cache: per-capability TTL keyed off each
  result's own `TimingClass`, plus a single-flight-on-miss lock (`SET NX EX`) with poll-and-wait
  against stampedes on a hot key. `dataaccess/composition.py::build_default_adapters` wraps every
  adapter with it (except the live-quote adapter) when `settings.market_data_cache_backend ==
  "redis"` (default `none`; `redis` in `docker-compose.yml`). Tests:
  `backend/tests/sources/test_caching_adapter.py`.
- **Durable derived store** — leader-writes-Parquet / workers-read-only (`ingest/store.py`'s
  `ParquetStore` + per-worker read-only DuckDB, [16-infrastructure.md](16-infrastructure.md) §3).

Not built: persistence of canonical packages / observation runs (they exist only in memory).

**Layering (only `sources/` does I/O):** `context/` requests a **capability for a
(symbol, market)** from the **data-access router**
([19-data-access-router.md](19-data-access-router.md)) — it never names a source or opens a
socket/DuckDB handle. The router resolves per-market priority + fallback and returns a
normalized result from the configured adapter(s). This is what makes `context/` market-agnostic.

## Canonical (Mode A / Level 1) — `canonical.py`

- Build and **validate** the immutable canonical market-context package.
- **Immutable in memory today:** `CanonicalPackage` is a frozen Pydantic model;
  `new_version()` returns a new object with `version + 1` rather than mutating in place
  (enforced by the framework, not just convention). This module performs **no I/O** — there is
  no durable canonical-package store; a caller that wants a canonical package persisted across requests has nowhere to
  write it to today.

## Bounded Observation (Mode B / Level 2) — `observation.py`

- One **explicit, operator-triggered** live-ish observation. No polling/scheduling: this module
  has no loop, timer or retry.
- Backed by the `run_bounded_observation` tool; free-source snapshots are `public`, paid
  (FinMind Sponsor) real-time is `sponsor` (entitlement-masked).
- Returns an immutable `ObservationRun` record (auditable); it is not itself persisted.

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
| M8A | Official EOD merge (TWSE OpenAPI + FinMind history; the TPEX adapter is not router-wired) | primary — in the default capability set |
| M8B | TAIFEX derivatives context (`derivatives_oi`, via the real `taifex_openapi` adapter) | built and configured, but **opt-in**: excluded from the default capability set, requested only via `BuildOptions.include_derivatives=True` |
| M8C | `TAIFEX_MIS` live-ish + real-time snapshots (`PRICE_INTRADAY`) | **staged behind `BuildOptions.enable_taifex_mis_validation`; no `TAIFEX_MIS` adapter exists** — `PRICE_INTRADAY` is currently served by `twse_mis`; `sponsor` where it uses paid FinMind data |

## Governance obligations

- Never mutate the canonical package.
- The conversation package must strip raw payloads and inject caveats before leaving this layer.
- M8C remains behind a validation flag until verified; its paid-data portions are `sponsor`
  (entitlement-masked), not approval-gated.
