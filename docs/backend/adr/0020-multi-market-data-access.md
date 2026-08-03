# ADR 0020 — Capability-based, multi-market data-access layer with per-market source priority

## Status
Accepted. Adds [modules/19](../modules/19-data-access-router.md); amends
[modules/01](../modules/01-sources.md) (SourceAdapter protocol), [modules/02](../modules/02-packages.md)
(context reads via router), [modules/18](../modules/18-calendar-events.md) (per-market rule-sets).

## Context
The design is TW-only, with `context/`/tools/calendar effectively knowing which source serves
what (FinMind for TW financials, yfinance for analyst, etc.). Adding markets (US/GB/JP) would
otherwise mean editing every caller and hard-coding source choices per market — not scalable.
The user wants per-market source **priority with fallback** (e.g. TW: FinMind>yfinance; US:
yfinance>FinMind) and cheap addition of markets/sources.

## Decision
Introduce a **data-access layer** (`dataaccess/`) between callers and `sources/`:
1. **Market & symbol first-class:** `Symbol(id, market)`; a `MarketProfile` per market carries
   currency/tz/trading-calendar/business-day rules + the calendar rule-set.
2. **Capabilities, not sources:** callers request a semantic **capability** (`price_eod`,
   `financials`, `analyst_consensus`, `calendar_events`, …) with a **normalized output model**;
   they never name a source or market-specific adapter.
3. **Adapters declare `supports() → {(capability, market)}`;** the router reads it.
4. **Priority-fallback router** driven by `config/source_priority.yaml`: `(market, capability) →
   ordered source list`. First **healthy + populated** result wins (coverage-gated); else next;
   else `unavailable` via M5Q. Optional per-capability **merge** policy (e.g. calendar unions
   feeds + rule engine). Result carries provenance (serving source + data_class + caveat).

## Consequences
- **Per-market priority is config** ("TW: FinMind>yfinance; US: yfinance>FinMind" = YAML rows);
  adding a market = a MarketProfile + priority block + calendar rule-set (no caller code); adding
  a source = one adapter + its id in priority lists.
- **Reliability for free:** source down/empty → automatic fallthrough (composes with circuit
  breakers, §16.6, and the no-empty-scaffolding gate).
- **Layering preserved:** router does no I/O (ADR-0002); `data_class` gate, cache tier (now
  keyed by `(capability, market, symbol)`), teaser, citations operate on normalized results.
- Callers become market- and source-agnostic — the biggest change is the SourceAdapter protocol
  gaining `supports()` and capability-typed `fetch`; the TW adapters keep working (they just
  declare their TW capability set).
- The product name/UX stays TW-first; the architecture no longer *assumes* single-market.
