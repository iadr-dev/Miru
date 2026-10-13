# Module: Data-access layer — capabilities, markets & source router (`backend/src/dataaccess/`)

The seam that makes the data layer **multi-market, maintainable, and extensible**. The data
layer now covers TW/US/GB/EU/JP (GLOBAL for datasets with no single-country home); more markets
add by config, not rewrite. Rather than let `context/`, tools, or the
calendar call adapters directly (which hard-codes "use FinMind here, yfinance there"), they ask
a **capability** for a **(market, symbol)** and a **router resolves which adapters to use, in
priority order, with fallback**. Adding a market or a source becomes a **config change**, not a
code change touching every caller.

Sits **between** the context/calendar/tools layers and `sources/` (preserves the one-way layering
rule: only `sources/` does I/O, and only `dataaccess/` and `ingest/` import it).

```
tools / context / calendar        "give me <capability> for <symbol@market>"
        │  (never name a source)
        ▼
   Data-access router  (dataaccess/router.py)
        │  resolve (market, capability) → ordered [adapters] from config
        │  try in priority order → first healthy/populated result wins; else next; else unavailable
        ▼
   sources/*  (adapters — the only I/O)
```

## 1. Market & symbol as first-class dimensions

A symbol is `(exchange_symbol, market)`, not a bare string. `market` drives routing, calendar
rules, currency, timezone, trading hours, and the source-priority table.

```python
class Market(str, Enum):
    TW = "TW"        # 上市/上櫃 (TWSE/TPEx)
    US = "US"; GB = "GB"; JP = "JP"
    HK = "HK"        # real enum value, but has no block in source_priority.yaml today; a live
                      # router call for any (HK, capability) pair degrades to unavailable
    EU = "EU"        # continental-Europe equities (e.g. Euronext) — no single-country home
    GLOBAL = "GLOBAL"  # datasets with no single-country home at all (multi-country central-bank
                        # rate panels, world commodity benchmarks) — distinct from a market gap
    ...   # add by config, not rewrite

class Symbol(BaseModel):
    id: str                 # canonical, market-scoped (e.g. "2330", "AAPL")
    market: Market
    yahoo: str | None       # provider-specific forms resolved by adapters (2330.TW, AAPL, 7203.T)
    finmind: str | None
```

Per-market currency/timezone/trading-calendar handling is not a router concern; see
[18](18-calendar-events.md) (`calendar/` is TW-shaped today).

## 2. Capabilities — *what*, not *which source*

Callers request a **capability** (a stable semantic contract with a typed result), never a
source. Capabilities are the vocabulary of the data layer:

```
price_eod (public, all tiers) · price_intraday (data_class per serving adapter; the 5s server-pushed stream is a Premium *feature*) ·
financials · dividends · corporate_actions ·
institutional_flow · margin_short · shareholding · chip_broker · analyst_consensus ·
esg · profile · news · calendar_events · etf_holdings · derivatives_oi · macro · sector_class ·
options_chain · institutional_holders · insider_activity · symbol_search · symbol_lookup ·
earnings_calendar · ipo_calendar · economic_calendar · sector_overview · industry_overview ·
market_status · fund_holdings · … (146 capabilities total across TW/US/GB/EU/JP/GLOBAL — this
list is illustrative, not exhaustive; the full enum lives in `schemas/enums.Capability`, and
each adapter's coverage is declared in its `supports()` under `backend/src/sources/`)
```

Each capability defines a **normalized output model** (e.g. `EODBar`, `DividendEvent`,
`AnalystConsensus`) so callers get the same shape regardless of which adapter served it.
Adapters declare which capabilities they provide **and for which markets** (below).

## 3. Adapters declare `(capability, market)` support

The `SourceAdapter` protocol gains coverage metadata — the router reads it, callers don't:

```python
class SourceAdapter(Protocol):
    source_id: str
    def supports(self) -> set[tuple[Capability, Market]]: ...   # what & where
    def fetch(self, cap: Capability, sym: Symbol, *, tls_policy, **params) -> SourceResult: ...
```

Examples: `finmind` → {(price_eod, TW), (financials, TW), (chip_broker, TW), (price_eod, US),
(price_eod, JP)…}; `yfinance` → {(analyst_consensus, TW/US/…), (price_eod, US/GB/JP…),
(esg, *), (sector_class, *)}; `twse_openapi` → {(price_eod, TW), …}. An adapter simply **doesn't
declare** a pair it can't serve — the router then won't route to it.

## 4. The priority-fallback router (the core of the design)

A config table maps **(market, capability) → ordered source list**. Resolution: try in order;
take the first result that is **healthy and populated** (coverage-gated — an empty/`unavailable`
result falls through, per the yfinance/analyst gating rule); else the next; else `unavailable`
surfaced via M5Q.

```yaml
# config/source_priority.yaml  — the single place "who serves what for which market" lives
TW:
  price_eod:          [finmind, twse_openapi]                # public — all tiers; FinMind first (backfill), TWSE latest-only fallback
  financials:         [finmind]                              # never yfinance for TW financials
  chip_broker:        [finmind]                              # sponsor; TW-only
  analyst_consensus:  [yfinance]                              # the licensed gap
  corporate_actions:  [mops]                                  # 重大訊息 material-announcement stream
  calendar_events:    [ncdr_closure, twse_openapi]            # merged via fetch_all, not first-healthy-wins (see 18-calendar-events.md)
  # The rule engine (`calendar/rules.py`) is not a router-resolvable adapter — it is a separate,
  # feedless Layer 1 composed above the router.
  # ... every TW capability listed above; see the live file for the full block

GLOBAL:               # datasets with no single-country home — Market.GLOBAL, not forced into TW/US
  central_bank_rate:  [finmind]
  gold_price:         [finmind]

# US/GB/EU/JP: policy is the mirror image of TW — yfinance is PRIMARY, FinMind fills in only the
# datasets it natively has for that market (modeled under distinct capability names, e.g.
# `us_stock_price`, so they don't compete with yfinance's `price_eod` row below).
US:
  us_stock_price:     [finmind]                               # FinMind's own US dataset, distinct capability name
  price_eod:          [yfinance]                              # yfinance primary for non-TW — no FinMind price_eod entry for US
  financials:         [yfinance]
  analyst_consensus:  [yfinance]
  options_chain:      [yfinance]                               # yfinance-native; no FinMind/gov equivalent at all
  chip_broker:        []                                       # N/A for US → capability absent
GB: { price_eod: [yfinance], financials: [yfinance], ... }
EU: { price_eod: [yfinance], financials: [yfinance], ... }
JP: { price_eod: [yfinance], financials: [yfinance], ... }
```

- **Per-market priority is data, not code** — "TW: FinMind>yfinance; US: yfinance>FinMind" is
  exactly this table. A new market = a new block; a new source = add its id to some lists.
- **Fallback + coverage-gating are built in** — matches the reliability requirements (circuit
  breakers [16](16-infrastructure.md) §6; don't render empty scaffolding). A source that is
  down/rate-limited/empty is skipped to the next automatically.
- **Merge policy.** `DataAccessRouter.fetch()` is strictly first-healthy-wins. For the rare
  capability whose adapters each contribute a disjoint slice (`calendar_events`: NCDR closures +
  TWSE schedules), `DataAccessRouter.fetch_all()` calls every configured adapter and returns every
  result; the caller merges. `calendar/events.py::get_calendar_events` also calls `fetch()` for
  `DIVIDENDS` and `CB_PUT_PROVISION` and merges those above the router, plus any caller-supplied
  rule-engine events ([18](18-calendar-events.md)).
- **Provenance:** the normalized result records which source actually served it + its
  `data_class`/`timing_class`/caveat, so the entitlement gate, teaser, and citations still work
  unchanged downstream.

## 5. Why this is maintainable / scalable / flexible

| Change | Cost |
|--------|------|
| Add a market (US/GB/JP) | a `Market` value + a block in `source_priority.yaml` (+ calendar rules if needed); **no caller code** |
| Add a source (e.g. Finnhub, Alpha Vantage) | one adapter declaring its `(capability, market)` set + add its id to priority lists |
| Change priority ("prefer X over Y for US") | edit one YAML line |
| A source dies / rate-limits | router falls through to the next; M5Q degrades — no outage |
| New capability | define its model + which adapters provide it; callers opt in |

Callers (context/tools/calendar) stay **market- and source-agnostic** — they name a capability
and a symbol; everything else is resolved by config. The `data_class` gate, caching tier
([16](16-infrastructure.md) §3, keyed by `(capability, market, symbol)`), and governance are
unchanged — they simply operate on the router's normalized results.

## Symbol search (`GET /search`)

`GET /search` (`api/routers/context.py::symbol_search`) dispatches two distinct backing tools,
never merged into one call: `market=TW` uses `get_symbol_directory_search` (a scan over the
ingested `TaiwanStockInfo` roster — stocks and ETFs together, warrants excluded); US/GB/JP/EU use
`get_symbol_search` (the `symbol_search` capability, served by `yfinance`, which refuses TW). Any
other market returns `501 not_supported`. There is no grouped TW → ETF → US result shape and no
主題/產業 match section.

## Governance
- Router lives above `sources/`; it performs **no I/O itself** (the one-way layering rule holds).
- Per-market currentness caveats and `data_class` propagate from the serving adapter; a
  cross-market answer is caveated per source. Non-trading rules are market-independent.
