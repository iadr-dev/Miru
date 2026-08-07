# Module: Data-access layer — capabilities, markets & source router (`backend/src/dataaccess/`)

The seam that makes the data layer **multi-market, maintainable, and extensible**. Today the
product is TW-only; tomorrow it adds US/GB/JP/… . Rather than let `context/`, tools, or the
calendar call adapters directly (which hard-codes "use FinMind here, yfinance there"), they ask
a **capability** for a **(market, symbol)** and a **router resolves which adapters to use, in
priority order, with fallback**. Adding a market or a source becomes a **config change**, not a
code change touching every caller.

Sits **between** the context/calendar/tools layers and `sources/` (preserves ADR-0002's one-way
rule + "only `sources/` does I/O"). See [../adr/0020](../adr/0020-multi-market-data-access.md).

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
    US = "US"; GB = "GB"; JP = "JP"; HK = "HK"; ...   # add by config, not rewrite

class Symbol(BaseModel):
    id: str                 # canonical, market-scoped (e.g. "2330", "AAPL")
    market: Market
    yahoo: str | None       # provider-specific forms resolved by adapters (2330.TW, AAPL, 7203.T)
    finmind: str | None
```

A `MarketProfile` (config) carries per-market currency, tz, trading calendar source, business-day
rules, and the calendar rule-set ([18](18-calendar-events.md)) — so TW's 除權息/結算 rules and
US's ex-div/OPEX rules live side by side, selected by `market`.

## 2. Capabilities — *what*, not *which source*

Callers request a **capability** (a stable semantic contract with a typed result), never a
source. Capabilities are the vocabulary of the data layer:

```
price_eod (public, all tiers) · price_intraday (data_class per serving adapter; the 5s server-pushed stream is a Premium *feature*) ·
financials · dividends · corporate_actions ·
institutional_flow · margin_short · shareholding · chip_broker · analyst_consensus ·
esg · profile · news · calendar_events · etf_holdings · derivatives_oi · macro · sector_class
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
  price_eod:          [finmind, twse_openapi, yfinance]     # public — all tiers
  price_intraday:     [finmind, twse_mis]                    # data_class per serving adapter: finmind=sponsor, twse_mis=public; the /quote/stream FEATURE is Premium (see gov §9b.5)
  financials:         [finmind, twse_openapi]               # never yfinance for TW financials
  chip_broker:        [finmind]                              # sponsor; TW-only
  analyst_consensus:  [yfinance]                             # the licensed gap
US:
  price_eod:          [yfinance, finmind]                    # US: yfinance first, FinMind fallback
  financials:         [yfinance]
  analyst_consensus:  [yfinance]
  chip_broker:        []                                     # N/A for US → capability absent
JP: { price_eod: [yfinance, finmind], ... }
```

- **Per-market priority is data, not code** — "TW: FinMind>yfinance; US: yfinance>FinMind" is
  exactly this table. A new market = a new block; a new source = add its id to some lists.
- **Fallback + coverage-gating are built in** — matches the reliability requirements (circuit
  breakers [16](16-infrastructure.md) §6; don't render empty scaffolding). A source that is
  down/rate-limited/empty is skipped to the next automatically.
- **Merge policy (optional):** some capabilities *merge* rather than first-wins. `calendar_events`
  is the real example — but it composes at the `calendar/` layer itself (`calendar/events.py`
  fetching `Capability.DIVIDENDS`/`Capability.CB_PUT_PROVISION` directly, plus the Layer-1 rule
  engine's own output), not via a `calendar_events`-registered source-priority row: there is no
  `calendar_ext`/`rule_engine` source id in the real router, and no `calendar_events` entry
  exists in `source_priority.yaml` for TW today (only `sources/ncdr_closure.py`, typhoon
  closures, registers that capability). See [18-calendar-events.md](18-calendar-events.md) for
  why composition happens above the router rather than through it — a `sources/` adapter may
  never call back into `dataaccess/` (it's the layer below).
- **Provenance:** the normalized result records which source actually served it + its
  `data_class`/`timing_class`/caveat, so the entitlement gate, teaser, and citations still work
  unchanged downstream.

## 5. Why this is maintainable / scalable / flexible

| Change | Cost |
|--------|------|
| Add a market (US/GB/JP) | a `MarketProfile` + rows in `source_priority.yaml` + calendar rule-set; **no caller code** |
| Add a source (e.g. Finnhub, Alpha Vantage) | one adapter declaring its `(capability, market)` set + add its id to priority lists |
| Change priority ("prefer X over Y for US") | edit one YAML line |
| A source dies / rate-limits | router falls through to the next; M5Q degrades — no outage |
| New capability | define its model + which adapters provide it; callers opt in |

Callers (context/tools/calendar) stay **market- and source-agnostic** — they name a capability
and a symbol; everything else is resolved by config. The `data_class` gate, caching tier
([16](16-infrastructure.md) §3, keyed by `(capability, market, symbol)`), and governance are
unchanged — they simply operate on the router's normalized results.

## Symbol universe & search (`GET /search`)

The router owns the **symbol universe** — the list of resolvable `(symbol, market, asset_class)`
tuples with display metadata (name, 產業/sector) — because it's the layer that knows `Market`/`Symbol`.
Sourced from ingested reference data (`TaiwanStockInfo` for TW stocks + ETF list; registered US
symbols for the phased US market), refreshed by the ingest layer ([22](22-data-ingest.md)), cached.

`GET /search?q=&market=` (public) is the design intent for the top-bar palette / mobile Stock
tab: match `q` against 代號 / 名稱 / 產業 (prefix-ranked above substring), return
`[{symbol, name, market, asset_class, sector}]` grouped TW → ETF → US, plus optional 主題/產業
match sections — a lookup over a cached universe, not a per-request source fetch.

**Implementation status.** Only non-TW markets (US/GB/JP/EU, via `yfinance.Search`/`Lookup`) are
actually wired. `market=TW` returns a clear `501 not_supported` rather than a naive whole-roster
scan masquerading as a ranked type-ahead — there is no ingested `TaiwanStockInfo`/ETF
symbol-universe search service backing a TW/ETF type-ahead today. The universe is
client-cacheable (long TTL) where it does work; the result feeds Stock Detail navigation and the
☆ add-to-watchlist affordance.

## Governance
- Router lives above `sources/`; it performs **no I/O itself** (ADR-0002 holds).
- Per-market currentness caveats and `data_class` propagate from the serving adapter; a
  cross-market answer is caveated per source. Non-trading rules are market-independent.
